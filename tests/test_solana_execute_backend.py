from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from gateway.journal import GatewayJournal
from gateway.protocol import GatewayError
from gateway.service import ExternalExecutionGateway
from gateway.solana_execute import (
    Ed25519AuthorizationVerifier,
    SolanaExecutionBackend,
)
from gateway.solana_prepare import ASSOCIATED_TOKEN_PROGRAM, TOKEN_PROGRAM

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)


def digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


class ExecutionRpc:
    endpoint = "https://api.devnet.solana.com"

    def __init__(
        self,
        *,
        signer: Pubkey,
        destination: Pubkey,
        mint: Pubkey,
    ) -> None:
        token_program = Pubkey.from_string(TOKEN_PROGRAM)
        ata_program = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM)
        self.source_ata = Pubkey.find_program_address(
            [bytes(signer), bytes(token_program), bytes(mint)],
            ata_program,
        )[0]
        self.destination_ata = Pubkey.find_program_address(
            [bytes(destination), bytes(token_program), bytes(mint)],
            ata_program,
        )[0]
        self.signer = signer
        self.destination = destination
        self.mint = mint
        self.blockhash = str(Hash.new_unique())
        self.block_height = 90
        self.signature_status: dict[str, Any] | None = None
        self.lose_send_response = False
        self.return_different_signature = False
        self.broadcasts: list[str] = []
        self.before_send: Callable[[str], None] | None = None
        self.methods: list[str] = []

    def call(self, method: str, params: list[Any]) -> dict[str, Any]:
        self.methods.append(method)
        if method == "getAccountInfo":
            address = params[0]
            if address == str(self.mint):
                info = {"decimals": 6}
            elif address == str(self.source_ata):
                info = {
                    "mint": str(self.mint),
                    "owner": str(self.signer),
                    "tokenAmount": {"amount": "9000000"},
                }
            elif address == str(self.destination_ata):
                info = {
                    "mint": str(self.mint),
                    "owner": str(self.destination),
                    "tokenAmount": {"amount": "0"},
                }
            else:
                return {"jsonrpc": "2.0", "result": {"value": None}}
            return {
                "jsonrpc": "2.0",
                "result": {
                    "value": {
                        "owner": TOKEN_PROGRAM,
                        "data": {"parsed": {"info": info}},
                    }
                },
            }
        if method == "getGenesisHash":
            return {"jsonrpc": "2.0", "result": str(Hash.new_unique())}
        if method == "getLatestBlockhash":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "context": {"slot": 88},
                    "value": {
                        "blockhash": self.blockhash,
                        "lastValidBlockHeight": 100,
                    },
                },
            }
        if method == "simulateTransaction":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "context": {"slot": 89},
                    "value": {
                        "err": None,
                        "logs": ["Program Tokenkeg success"],
                        "preBalances": [10000, 1, 1, 1],
                        "postBalances": [5000, 1, 1, 1],
                        "accounts": [],
                        "unitsConsumed": 1714,
                        "fee": 5000,
                    },
                },
            }
        if method == "getBlockHeight":
            return {"jsonrpc": "2.0", "result": self.block_height}
        if method == "sendTransaction":
            transaction_base64 = params[0]
            transaction = VersionedTransaction.from_bytes(base64.b64decode(transaction_base64, validate=True))
            signature = str(transaction.signatures[0])
            if self.before_send is not None:
                self.before_send(signature)
            self.broadcasts.append(transaction_base64)
            if self.lose_send_response:
                raise GatewayError("rpc_failure", "test response lost")
            if self.return_different_signature:
                signature = str(Keypair().sign_message(b"different"))
            return {"jsonrpc": "2.0", "result": signature}
        if method == "getSignatureStatuses":
            return {
                "jsonrpc": "2.0",
                "result": {"context": {"slot": 91}, "value": [self.signature_status]},
            }
        raise AssertionError(f"unexpected RPC method: {method}")


def prepare_payload(
    signer: Pubkey,
    destination: Pubkey,
    mint: Pubkey,
) -> dict[str, Any]:
    plan = {
        "protocol_version": "1.0.0",
        "normalization_profile": "foundry-pay-domain-v1",
        "obligation_id": "obl_exec_001",
        "network": "solana:devnet",
        "capability": "solana.spl_transfer.v1",
        "asset": {"kind": "spl-token", "mint": str(mint), "decimals": 6},
        "amount_base_units": "1000000",
        "source": str(signer),
        "destination": str(destination),
        "expires_at": "2026-07-23T18:00:00Z",
    }
    plan_hash = digest(plan)
    return {
        "request": {
            "type": "external_execution_request",
            "protocol_version": "1.0.0",
            "execution_request_id": "exec_001",
            "idempotency_key": "idem_exec_001",
            "economic_plan": plan,
            "economic_plan_hash": plan_hash,
            "economic_approval": {
                "approval_id": "approval_exec_001",
                "economic_plan_hash": plan_hash,
                "approved_by": "operator_001",
                "issued_at": "2026-07-23T15:00:00Z",
                "expires_at": "2026-07-23T17:00:00Z",
            },
        },
        "preparation_context": {
            "constraints": {
                "max_fee_lamports": 50000,
                "allowed_programs": [TOKEN_PROGRAM],
            }
        },
    }


def authorization(
    prepared: dict[str, Any],
    authority: Keypair,
    **changes: Any,
) -> dict[str, Any]:
    value = {
        "type": "execution_authorization",
        "protocol_version": "1.0.0",
        "authorization_id": "auth_exec_001",
        "execution_request_id": prepared["execution_request_id"],
        "execution_commitment_hash": prepared["execution_commitment_hash"],
        "prepared_message_hash": prepared["prepared_message_hash"],
        "signer": prepared["signer"],
        "single_use": True,
        "issued_at": "2026-07-23T16:00:00Z",
        "expires_at": "2026-07-23T16:00:30Z",
    }
    value.update(changes)
    value["authorization_signature"] = str(authority.sign_message(rfc8785.dumps(value)))
    return value


def execution_payload(
    prepared: dict[str, Any],
    authority: Keypair,
    asset_signer: Keypair,
    *,
    execution_authorization: dict[str, Any] | None = None,
    message_base64: str | None = None,
) -> dict[str, Any]:
    selected_message = message_base64 or prepared["prepared_message_base64"]
    message = base64.b64decode(selected_message, validate=True)
    return {
        "execution_request_id": prepared["execution_request_id"],
        "prepared_message_base64": selected_message,
        "execution_authorization": execution_authorization or authorization(prepared, authority),
        "message_signature": {
            "signer": str(asset_signer.pubkey()),
            "signature": str(asset_signer.sign_message(message)),
        },
    }


def backend(
    path: Path,
    *,
    asset_signer: Keypair,
    destination: Pubkey,
    mint: Pubkey,
    authority: Keypair,
    rpc: ExecutionRpc,
) -> SolanaExecutionBackend:
    return SolanaExecutionBackend(
        journal_path=path,
        signer=str(asset_signer.pubkey()),
        authorization_verifier=Ed25519AuthorizationVerifier(str(authority.pubkey())),
        rpc=rpc,
        now=lambda: NOW,
    )


@pytest.fixture
def actors() -> tuple[Keypair, Pubkey, Pubkey, Keypair]:
    return Keypair(), Pubkey.new_unique(), Pubkey.new_unique(), Keypair()


def prepared_fixture(
    path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> tuple[SolanaExecutionBackend, ExecutionRpc, dict[str, Any]]:
    asset_signer, destination, mint, authority = actors
    rpc = ExecutionRpc(
        signer=asset_signer.pubkey(),
        destination=destination,
        mint=mint,
    )
    subject = backend(
        path,
        asset_signer=asset_signer,
        destination=destination,
        mint=mint,
        authority=authority,
        rpc=rpc,
    )
    prepared = subject.prepare(prepare_payload(asset_signer.pubkey(), destination, mint))
    return subject, rpc, prepared


def test_signature_is_persisted_before_single_broadcast(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    path = tmp_path / "gateway.sqlite3"
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(path, actors)
    expected_signature = str(
        asset_signer.sign_message(base64.b64decode(prepared["prepared_message_base64"], validate=True))
    )

    def assert_persisted(signature: str) -> None:
        row = subject.executions.get("exec_001")
        assert row is not None
        assert row["state"] == "broadcast_started"
        assert row["signature"] == signature == expected_signature
        assert row["signature_persisted_at"] is not None

    rpc.before_send = assert_persisted
    receipt = subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))

    assert receipt["state"] == "submitted"
    assert receipt["signature"] == expected_signature
    assert len(rpc.broadcasts) == 1
    sent = VersionedTransaction.from_bytes(base64.b64decode(rpc.broadcasts[0], validate=True))
    assert to_bytes_versioned(sent.message) == base64.b64decode(
        prepared["prepared_message_base64"],
        validate=True,
    )
    assert subject.executions.get("exec_001")["state"] == "submitted"  # type: ignore[index]


def test_tampered_authorization_and_message_never_broadcast(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(tmp_path / "gateway.sqlite3", actors)
    invalid_authorization = authorization(prepared, authority)
    invalid_authorization["execution_commitment_hash"] = "sha256:" + "1" * 64

    with pytest.raises(GatewayError, match="not bound"):
        subject.authorize_and_execute(
            execution_payload(
                prepared,
                authority,
                asset_signer,
                execution_authorization=invalid_authorization,
            )
        )

    message = bytearray(base64.b64decode(prepared["prepared_message_base64"], validate=True))
    message[-1] ^= 1
    with pytest.raises(GatewayError, match="differs"):
        subject.authorize_and_execute(
            execution_payload(
                prepared,
                authority,
                asset_signer,
                message_base64=base64.b64encode(message).decode("ascii"),
            )
        )
    assert rpc.broadcasts == []


def test_invalid_authority_or_message_signature_never_broadcast(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(tmp_path / "gateway.sqlite3", actors)
    invalid_authorization = authorization(prepared, authority)
    invalid_authorization["authorization_signature"] = str(Keypair().sign_message(b"wrong"))
    with pytest.raises(GatewayError, match="verification failed"):
        subject.authorize_and_execute(
            execution_payload(
                prepared,
                authority,
                asset_signer,
                execution_authorization=invalid_authorization,
            )
        )

    payload = execution_payload(prepared, authority, asset_signer)
    payload["message_signature"]["signature"] = str(Keypair().sign_message(b"wrong"))
    with pytest.raises(GatewayError, match="does not verify"):
        subject.authorize_and_execute(payload)
    assert rpc.broadcasts == []


def test_expired_authorization_or_blockhash_never_broadcast(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(tmp_path / "gateway.sqlite3", actors)
    expired = authorization(
        prepared,
        authority,
        issued_at="2026-07-23T15:59:00Z",
        expires_at="2026-07-23T16:00:00Z",
    )

    with pytest.raises(GatewayError, match="expired"):
        subject.authorize_and_execute(
            execution_payload(
                prepared,
                authority,
                asset_signer,
                execution_authorization=expired,
            )
        )

    rpc.block_height = 101
    with pytest.raises(GatewayError, match="blockhash has expired"):
        subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))
    assert rpc.broadcasts == []


def test_lost_send_response_recovers_by_signature_without_rebroadcast(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    path = tmp_path / "gateway.sqlite3"
    asset_signer, destination, mint, authority = actors
    subject, rpc, prepared = prepared_fixture(path, actors)
    rpc.lose_send_response = True

    with pytest.raises(GatewayError) as captured:
        subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))
    assert captured.value.code == "needs_recovery"
    persisted = subject.executions.get("exec_001")
    assert persisted is not None
    assert persisted["state"] == "needs_recovery"
    assert len(rpc.broadcasts) == 1

    rpc.lose_send_response = False
    rpc.signature_status = {
        "slot": 123456,
        "confirmations": 1,
        "err": None,
        "confirmationStatus": "confirmed",
    }
    restarted = backend(
        path,
        asset_signer=asset_signer,
        destination=destination,
        mint=mint,
        authority=authority,
        rpc=rpc,
    )
    recovery = restarted.recover({"execution_request_id": "exec_001"})

    assert recovery["outcome"] == "recovered_confirmed"
    assert recovery["signature"] == persisted["signature"]
    assert len(rpc.broadcasts) == 1
    assert restarted.evidence({"execution_request_id": "exec_001"})["execution"]["state"] == "confirmed"


def test_replay_after_unknown_outcome_never_calls_send_again(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(tmp_path / "gateway.sqlite3", actors)
    request = execution_payload(prepared, authority, asset_signer)
    rpc.lose_send_response = True
    with pytest.raises(GatewayError):
        subject.authorize_and_execute(request)

    rpc.lose_send_response = False
    with pytest.raises(GatewayError) as captured:
        subject.authorize_and_execute(request)

    assert captured.value.code == "needs_recovery"
    assert len(rpc.broadcasts) == 1


def test_gateway_recovers_lost_broadcast_response_without_redispatch(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    path = tmp_path / "gateway.sqlite3"
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(path, actors)
    gateway = ExternalExecutionGateway(GatewayJournal(path), subject)
    execute_request = {
        "gateway_protocol_version": "1.0.0",
        "gateway_request_id": "gw_execute_001",
        "command": "authorize-and-execute",
        "payload": execution_payload(prepared, authority, asset_signer),
    }
    rpc.lose_send_response = True

    lost = gateway.handle(execute_request)
    replay = gateway.handle(execute_request)

    assert lost["ok"] is False
    assert lost["error"]["code"] == "needs_recovery"
    assert replay["replayed"] is True
    assert len(rpc.broadcasts) == 1

    rpc.lose_send_response = False
    rpc.signature_status = {
        "slot": 123456,
        "confirmations": 1,
        "err": None,
        "confirmationStatus": "confirmed",
    }
    recovered = gateway.handle(
        {
            "gateway_protocol_version": "1.0.0",
            "gateway_request_id": "gw_recover_001",
            "command": "recover",
            "payload": {"execution_request_id": "exec_001"},
        }
    )

    assert recovered["ok"] is True
    assert recovered["result"]["outcome"] == "recovered_confirmed"
    assert len(rpc.broadcasts) == 1


def test_status_confirmation_and_evidence_survive_restart(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    path = tmp_path / "gateway.sqlite3"
    asset_signer, destination, mint, authority = actors
    subject, rpc, prepared = prepared_fixture(path, actors)
    receipt = subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))
    rpc.signature_status = {
        "slot": 123456,
        "confirmations": None,
        "err": None,
        "confirmationStatus": "finalized",
    }

    restarted = backend(
        path,
        asset_signer=asset_signer,
        destination=destination,
        mint=mint,
        authority=authority,
        rpc=rpc,
    )
    status = restarted.status({"execution_request_id": "exec_001"})
    evidence = restarted.evidence({"execution_request_id": "exec_001"})

    assert status["state"] == "confirmed"
    assert status["signature"] == receipt["signature"]
    assert evidence["execution"]["state"] == "confirmed"
    assert evidence["execution"]["receipt"]["state"] == "confirmed"
    assert len(rpc.broadcasts) == 1


def test_unseen_signature_after_expiry_still_requires_independent_reconciliation(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, _, _, authority = actors
    subject, rpc, prepared = prepared_fixture(tmp_path / "gateway.sqlite3", actors)
    subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))
    rpc.signature_status = None
    rpc.block_height = 101

    recovery = subject.recover({"execution_request_id": "exec_001"})

    assert recovery["outcome"] == "not_found_after_expiry_needs_reconciliation"
    assert recovery["may_rematerialize"] is False
    assert subject.status({"execution_request_id": "exec_001"})["state"] == "needs_recovery"
    assert len(rpc.broadcasts) == 1


def test_execute_fails_closed_without_foundry_verifier(
    tmp_path: Path,
    actors: tuple[Keypair, Pubkey, Pubkey, Keypair],
) -> None:
    asset_signer, destination, mint, authority = actors
    rpc = ExecutionRpc(
        signer=asset_signer.pubkey(),
        destination=destination,
        mint=mint,
    )
    subject = SolanaExecutionBackend(
        journal_path=tmp_path / "gateway.sqlite3",
        signer=str(asset_signer.pubkey()),
        authorization_verifier=None,
        rpc=rpc,
        now=lambda: NOW,
    )
    prepared = subject.prepare(prepare_payload(asset_signer.pubkey(), destination, mint))

    with pytest.raises(GatewayError, match="verification is required"):
        subject.authorize_and_execute(execution_payload(prepared, authority, asset_signer))
    assert rpc.broadcasts == []
