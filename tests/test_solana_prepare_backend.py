from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from solders.hash import Hash
from solders.pubkey import Pubkey

from gateway.protocol import GatewayError
from gateway.solana_prepare import (
    ASSOCIATED_TOKEN_PROGRAM,
    TOKEN_PROGRAM,
    SolanaPreparationBackend,
)

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)


def digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


class FakeRpc:
    endpoint = "https://api.devnet.solana.com"

    def __init__(
        self,
        *,
        signer: Pubkey,
        destination: Pubkey,
        mint: Pubkey,
        decimals: int = 6,
        simulation_error: Any = None,
        block_height: int = 90,
    ) -> None:
        token_program = Pubkey.from_string(TOKEN_PROGRAM)
        ata_program = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM)
        self.source_ata = Pubkey.find_program_address([bytes(signer), bytes(token_program), bytes(mint)], ata_program)[
            0
        ]
        self.destination_ata = Pubkey.find_program_address(
            [bytes(destination), bytes(token_program), bytes(mint)], ata_program
        )[0]
        self.mint = mint
        self.signer = signer
        self.destination = destination
        self.decimals = decimals
        self.simulation_error = simulation_error
        self.block_height = block_height
        self.blockhash = str(Hash.new_unique())
        self.methods: list[str] = []

    def call(self, method: str, params: list[Any]) -> dict[str, Any]:
        self.methods.append(method)
        if method == "getAccountInfo":
            address = params[0]
            if address == str(self.mint):
                info = {"decimals": self.decimals}
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
                        "err": self.simulation_error,
                        "logs": ["Program Tokenkeg success"],
                        "preBalances": [10000, 1, 1, 1],
                        "postBalances": [5000, 1, 1, 1],
                        "accounts": [{"data": ["AA==", "base64"]}],
                        "unitsConsumed": 1714,
                        "fee": 5000,
                    },
                },
            }
        if method == "getBlockHeight":
            return {"jsonrpc": "2.0", "result": self.block_height}
        raise AssertionError(f"unexpected RPC method: {method}")


def payload(
    signer: Pubkey,
    destination: Pubkey,
    mint: Pubkey,
    *,
    amount: str = "1000000",
) -> dict[str, Any]:
    plan = {
        "protocol_version": "1.0.0",
        "normalization_profile": "foundry-pay-domain-v1",
        "obligation_id": "obl_001",
        "network": "solana:devnet",
        "capability": "solana.spl_transfer.v1",
        "asset": {"kind": "spl-token", "mint": str(mint), "decimals": 6},
        "amount_base_units": amount,
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
            "idempotency_key": "idem_001",
            "economic_plan": plan,
            "economic_plan_hash": plan_hash,
            "economic_approval": {
                "approval_id": "approval_001",
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


@pytest.fixture
def keys() -> tuple[Pubkey, Pubkey, Pubkey]:
    return Pubkey.new_unique(), Pubkey.new_unique(), Pubkey.new_unique()


def backend(
    path: Path,
    keys: tuple[Pubkey, Pubkey, Pubkey],
    *,
    rpc: FakeRpc | None = None,
    now: datetime = NOW,
) -> tuple[SolanaPreparationBackend, FakeRpc]:
    signer, destination, mint = keys
    selected_rpc = rpc or FakeRpc(signer=signer, destination=destination, mint=mint)
    return (
        SolanaPreparationBackend(
            journal_path=path,
            signer=str(signer),
            rpc=selected_rpc,
            now=lambda: now,
        ),
        selected_rpc,
    )


def test_prepare_materializes_exact_message_and_never_broadcasts(
    tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]
) -> None:
    subject, rpc = backend(tmp_path / "gateway.sqlite3", keys)

    prepared = subject.prepare(payload(*keys))
    message = base64.b64decode(prepared["prepared_message_base64"])

    assert prepared["type"] == "prepared_execution"
    assert prepared["prepared_message_hash"] == (f"sha256:{hashlib.sha256(message).hexdigest()}")
    assert prepared["simulation"]["success"] is True
    assert prepared["execution_commitment_hash"].startswith("sha256:")
    assert rpc.methods.count("simulateTransaction") == 1
    assert "sendTransaction" not in rpc.methods
    assert "requestAirdrop" not in rpc.methods


def test_status_recover_and_evidence_survive_restart(tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]) -> None:
    path = tmp_path / "gateway.sqlite3"
    first, rpc = backend(path, keys)
    prepared = first.prepare(payload(*keys))
    restarted, _ = backend(path, keys, rpc=rpc)

    status = restarted.status({"execution_request_id": "exec_001"})
    evidence = restarted.evidence({"execution_request_id": "exec_001"})
    recovery = restarted.recover({"execution_request_id": "exec_001"})

    assert status["state"] == "prepared"
    assert evidence["prepared_execution"] == prepared
    assert evidence["evidence"]["local_policy"]["decision"] == "allow"
    assert recovery["outcome"] == "failed_before_broadcast"
    assert recovery["may_rematerialize"] is False


def test_same_execution_replays_without_another_rpc_call(tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]) -> None:
    subject, rpc = backend(tmp_path / "gateway.sqlite3", keys)
    first = subject.prepare(payload(*keys))
    call_count = len(rpc.methods)

    second = subject.prepare(payload(*keys))

    assert second == first
    assert len(rpc.methods) == call_count


@pytest.mark.parametrize(
    "field,value",
    [
        ("network", "solana:mainnet"),
        ("capability", "solana.system_transfer.v1"),
        ("destination", str(Pubkey.new_unique())),
        ("amount_base_units", "2000000"),
    ],
)
def test_material_plan_tampering_fails_closed(
    tmp_path: Path,
    keys: tuple[Pubkey, Pubkey, Pubkey],
    field: str,
    value: str,
) -> None:
    subject, _ = backend(tmp_path / f"{field}.sqlite3", keys)
    request = payload(*keys)
    request["request"]["economic_plan"][field] = value

    with pytest.raises(GatewayError):
        subject.prepare(request)


def test_reapproved_forbidden_network_is_still_blocked_by_local_policy(
    tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]
) -> None:
    subject, _ = backend(tmp_path / "network.sqlite3", keys)
    request = payload(*keys)
    request["request"]["economic_plan"]["network"] = "solana:mainnet"
    changed_hash = digest(request["request"]["economic_plan"])
    request["request"]["economic_plan_hash"] = changed_hash
    request["request"]["economic_approval"]["economic_plan_hash"] = changed_hash

    with pytest.raises(GatewayError, match="not permitted"):
        subject.prepare(request)


def test_non_allowlisted_program_and_simulation_failure_create_no_preparation(
    tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]
) -> None:
    path = tmp_path / "gateway.sqlite3"
    subject, _ = backend(path, keys)
    blocked = payload(*keys)
    blocked["preparation_context"]["constraints"]["allowed_programs"] = [str(Pubkey.new_unique())]
    with pytest.raises(GatewayError, match="allowed_programs"):
        subject.prepare(blocked)

    failing_rpc = FakeRpc(
        signer=keys[0], destination=keys[1], mint=keys[2], simulation_error={"InstructionError": [0, 1]}
    )
    failing, _ = backend(path, keys, rpc=failing_rpc)
    with pytest.raises(GatewayError, match="simulation failed"):
        failing.prepare(payload(*keys))
    assert failing.store.get("exec_001") is None


def test_any_message_change_changes_hash(tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]) -> None:
    first, _ = backend(tmp_path / "first.sqlite3", keys)
    second, _ = backend(tmp_path / "second.sqlite3", keys)
    request_two = payload(*keys, amount="1000001")
    request_two["request"]["economic_plan_hash"] = digest(request_two["request"]["economic_plan"])
    request_two["request"]["economic_approval"]["economic_plan_hash"] = request_two["request"]["economic_plan_hash"]

    one = first.prepare(payload(*keys))
    two = second.prepare(request_two)

    assert one["prepared_message_hash"] != two["prepared_message_hash"]
    assert one["execution_commitment_hash"] != two["execution_commitment_hash"]


def test_expired_blockhash_marks_status_and_allows_rematerialization(
    tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]
) -> None:
    rpc = FakeRpc(signer=keys[0], destination=keys[1], mint=keys[2])
    subject, _ = backend(tmp_path / "gateway.sqlite3", keys, rpc=rpc)
    subject.prepare(payload(*keys))
    rpc.block_height = 101

    assert subject.status({"execution_request_id": "exec_001"})["state"] == "expired"
    assert subject.recover({"execution_request_id": "exec_001"})["may_rematerialize"] is True

    replacement = payload(*keys)
    replacement["request"]["execution_request_id"] = "exec_002"
    replacement["request"]["idempotency_key"] = "idem_002"
    rpc.block_height = 90
    rpc.blockhash = str(Hash.new_unique())
    prepared_again = subject.prepare(replacement)

    original = subject.evidence({"execution_request_id": "exec_001"})["prepared_execution"]
    assert prepared_again["execution_request_id"] == "exec_002"
    assert prepared_again["prepared_message_hash"] != original["prepared_message_hash"]


def test_time_expiry_is_short_lived(tmp_path: Path, keys: tuple[Pubkey, Pubkey, Pubkey]) -> None:
    path = tmp_path / "gateway.sqlite3"
    first, rpc = backend(path, keys)
    first.prepare(payload(*keys))
    later, _ = backend(path, keys, rpc=rpc, now=NOW + timedelta(seconds=61))

    assert later.status({"execution_request_id": "exec_001"})["state"] == "expired"
