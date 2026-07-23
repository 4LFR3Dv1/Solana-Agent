"""SPL transfer preparation and simulation without signing or broadcasting."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import rfc8785
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from gateway.protocol import GatewayError
from solana_agent.adapters.solana_rpc import RpcTransport, UrllibRpcTransport

DEVNET_ENDPOINT = "https://api.devnet.solana.com"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
EXECUTOR_VERSION = "0.2.0"
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_AMOUNT = re.compile(r"^(0|[1-9][0-9]*)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PreparationRpc(Protocol):
    endpoint: str

    def call(self, method: str, params: list[Any]) -> dict[str, Any]: ...


@dataclass
class JsonRpcClient:
    endpoint: str = DEVNET_ENDPOINT
    transport: RpcTransport | None = None
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        if self.endpoint != DEVNET_ENDPOINT:
            raise ValueError("SA-GW-002 only permits the canonical devnet RPC")
        if self.transport is None:
            self.transport = UrllibRpcTransport()

    def call(self, method: str, params: list[Any]) -> dict[str, Any]:
        assert self.transport is not None
        response = self.transport.call(
            self.endpoint,
            {"jsonrpc": "2.0", "id": f"gateway-{method}", "method": method, "params": params},
            self.timeout_seconds,
        )
        if response.get("error") is not None:
            raise GatewayError(
                "rpc_failure",
                f"Solana RPC {method} failed",
                retryable=True,
                details={"method": method},
            )
        result = response.get("result")
        if not isinstance(result, (dict, str, int)):
            raise GatewayError("invalid_rpc_response", f"Solana RPC {method} returned invalid data")
        return response


class PreparationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prepared_executions (
                    execution_request_id TEXT PRIMARY KEY,
                    obligation_id TEXT NOT NULL,
                    economic_plan_hash TEXT NOT NULL,
                    prepared_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    last_valid_block_height INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS prepared_executions_obligation
                ON prepared_executions (obligation_id, updated_at)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(
        self,
        *,
        execution_request_id: str,
        obligation_id: str,
        economic_plan_hash: str,
        prepared: dict[str, Any],
        evidence: dict[str, Any],
        last_valid_block_height: int,
    ) -> dict[str, Any]:
        encoded = _json(prepared)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM prepared_executions
                WHERE execution_request_id = ?
                   OR (obligation_id = ? AND state != 'expired')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (execution_request_id, obligation_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["execution_request_id"] == execution_request_id
                    and existing["economic_plan_hash"] == economic_plan_hash
                ):
                    return json.loads(existing["prepared_json"])
                raise GatewayError(
                    "preparation_conflict",
                    "execution_request_id or obligation_id already has a different preparation",
                )
            connection.execute(
                """
                INSERT INTO prepared_executions VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)
                """,
                (
                    execution_request_id,
                    obligation_id,
                    economic_plan_hash,
                    encoded,
                    _json(evidence),
                    last_valid_block_height,
                    _now_text(),
                ),
            )
        return prepared

    def replay_or_block(
        self,
        *,
        execution_request_id: str,
        obligation_id: str,
        economic_plan_hash: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM prepared_executions
                WHERE execution_request_id = ?
                   OR (obligation_id = ? AND state != 'expired')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (execution_request_id, obligation_id),
            ).fetchone()
        if existing is None:
            return None
        if (
            existing["execution_request_id"] == execution_request_id
            and existing["economic_plan_hash"] == economic_plan_hash
        ):
            return json.loads(existing["prepared_json"])
        raise GatewayError(
            "preparation_conflict",
            "execution_request_id or obligation_id already has an active preparation",
        )

    def get(self, execution_request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM prepared_executions WHERE execution_request_id = ?",
                (execution_request_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def expire(self, execution_request_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE prepared_executions SET state = 'expired', updated_at = ?
                WHERE execution_request_id = ?
                """,
                (_now_text(), execution_request_id),
            )


class SolanaPreparationBackend:
    """External execution backend limited to PREPARE and read-only inspection."""

    def __init__(
        self,
        *,
        journal_path: Path,
        signer: str,
        rpc: PreparationRpc | None = None,
        executor_id: str = "solana-agent",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.signer = _pubkey(signer, "signer")
        if not _IDENTIFIER.fullmatch(executor_id):
            raise ValueError("executor_id is not canonical")
        self.executor_id = executor_id
        self.rpc = rpc or JsonRpcClient()
        self.store = PreparationStore(journal_path)
        self._now = now or (lambda: datetime.now(UTC))

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        request, context = _validate_prepare_payload(payload, self._now())
        plan = request["economic_plan"]
        replay = self.store.replay_or_block(
            execution_request_id=request["execution_request_id"],
            obligation_id=plan["obligation_id"],
            economic_plan_hash=request["economic_plan_hash"],
        )
        if replay is not None:
            return replay
        if str(self.signer) != plan["source"]:
            raise GatewayError("local_policy_block", "configured signer must equal economic source")

        constraints = context["constraints"]
        if constraints["allowed_programs"] != [TOKEN_PROGRAM]:
            raise GatewayError(
                "local_policy_block",
                "allowed_programs must contain only the canonical SPL Token program",
            )

        mint = _pubkey(plan["asset"]["mint"], "mint")
        source_owner = _pubkey(plan["source"], "source")
        destination_owner = _pubkey(plan["destination"], "destination")
        source_ata = _ata(source_owner, mint)
        destination_ata = _ata(destination_owner, mint)
        amount = int(plan["amount_base_units"])
        decimals = plan["asset"]["decimals"]

        self._validate_chain_accounts(
            mint=mint,
            decimals=decimals,
            source_owner=source_owner,
            destination_owner=destination_owner,
            source_ata=source_ata,
            destination_ata=destination_ata,
            amount=amount,
        )
        genesis_hash = _result_string(self.rpc.call("getGenesisHash", []), "getGenesisHash")
        blockhash_response = self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash_result = _result_object(blockhash_response, "getLatestBlockhash")
        blockhash_value = _object(blockhash_result.get("value"), "latest blockhash value")
        slot = _safe_int(_object(blockhash_result.get("context"), "blockhash context").get("slot"), "slot")
        recent_blockhash = _string(blockhash_value.get("blockhash"), "recent blockhash")
        last_valid_height = _safe_int(blockhash_value.get("lastValidBlockHeight"), "last valid block height")

        instruction = Instruction(
            Pubkey.from_string(TOKEN_PROGRAM),
            bytes([12]) + struct.pack("<Q", amount) + bytes([decimals]),
            [
                AccountMeta(source_ata, False, True),
                AccountMeta(mint, False, False),
                AccountMeta(destination_ata, False, True),
                AccountMeta(self.signer, True, False),
            ],
        )
        message = MessageV0.try_compile(
            self.signer,
            [instruction],
            [],
            Hash.from_string(recent_blockhash),
        )
        message_bytes = to_bytes_versioned(message)
        unsigned_transaction = VersionedTransaction.populate(message, [Signature.default()])
        simulation_response = self.rpc.call(
            "simulateTransaction",
            [
                base64.b64encode(bytes(unsigned_transaction)).decode("ascii"),
                {
                    "commitment": "confirmed",
                    "encoding": "base64",
                    "sigVerify": False,
                    "replaceRecentBlockhash": False,
                    "accounts": {
                        "encoding": "base64",
                        "addresses": [str(source_ata), str(destination_ata)],
                    },
                },
            ],
        )
        simulation_result = _result_object(simulation_response, "simulateTransaction")
        simulation_context = _object(simulation_result.get("context"), "simulation context")
        simulation_value = _object(simulation_result.get("value"), "simulation value")
        if simulation_value.get("err") is not None:
            raise GatewayError(
                "simulation_failed",
                "Solana transaction simulation failed",
                details={"error": simulation_value["err"]},
            )

        now = self._now().replace(microsecond=0)
        plan_expiry = _parse_timestamp(plan["expires_at"])
        valid_until = min(plan_expiry, now + timedelta(seconds=60))
        simulation = {
            "rpc_provider_id": "solana-devnet-public",
            "genesis_hash": genesis_hash,
            "slot": _safe_int(simulation_context.get("slot", slot), "simulation slot"),
            "commitment_level": "confirmed",
            "recent_blockhash": recent_blockhash,
            "last_valid_block_height": last_valid_height,
            "simulated_at": _timestamp(now),
            "valid_until": _timestamp(valid_until),
            "logs_hash": _hash_json(simulation_value.get("logs") or []),
            "pre_balances_hash": _hash_json(simulation_value.get("preBalances") or []),
            "post_balances_hash": _hash_json(simulation_value.get("postBalances") or []),
            "accounts_observed_hash": _hash_json(simulation_value.get("accounts") or []),
            "programs_observed_hash": _hash_json([TOKEN_PROGRAM]),
            "units_consumed": _safe_int(simulation_value.get("unitsConsumed", 0), "units consumed"),
            "fee_lamports": _safe_int(simulation_value.get("fee", 0), "fee"),
            "success": True,
        }
        if simulation["fee_lamports"] > constraints["max_fee_lamports"]:
            raise GatewayError("local_policy_block", "simulated fee exceeds max_fee_lamports")

        message_hash = _hash_bytes(message_bytes)
        simulation_hash = _hash_json(simulation)
        prepared = {
            "type": "prepared_execution",
            "protocol_version": "1.0.0",
            "execution_request_id": request["execution_request_id"],
            "executor_id": self.executor_id,
            "executor_version": EXECUTOR_VERSION,
            "economic_plan_hash": request["economic_plan_hash"],
            "prepared_message_base64": base64.b64encode(message_bytes).decode("ascii"),
            "prepared_message_hash": message_hash,
            "simulation": simulation,
            "simulation_attestation_hash": simulation_hash,
            "execution_commitment_hash": _hash_json(
                {
                    "protocol_version": "1.0.0",
                    "normalization_profile": "foundry-pay-domain-v1",
                    "execution_request_id": request["execution_request_id"],
                    "obligation_id": plan["obligation_id"],
                    "executor_id": self.executor_id,
                    "executor_version": EXECUTOR_VERSION,
                    "economic_plan_hash": request["economic_plan_hash"],
                    "prepared_message_hash": message_hash,
                    "simulation_attestation_hash": simulation_hash,
                    "signer": str(self.signer),
                    "constraints": constraints,
                    "expires_at": _timestamp(valid_until),
                }
            ),
            "signer": str(self.signer),
            "constraints": constraints,
            "expires_at": _timestamp(valid_until),
        }
        evidence = {
            "local_policy": {"decision": "allow", "rule_id": "spl-transfer-prepare-v1"},
            "source_token_account": str(source_ata),
            "destination_token_account": str(destination_ata),
            "programs": [TOKEN_PROGRAM],
            "rpc_endpoint": self.rpc.endpoint,
        }
        return self.store.save(
            execution_request_id=request["execution_request_id"],
            obligation_id=plan["obligation_id"],
            economic_plan_hash=request["economic_plan_hash"],
            prepared=prepared,
            evidence=evidence,
            last_valid_block_height=last_valid_height,
        )

    def status(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        prepared = json.loads(row["prepared_json"])
        state = row["state"]
        if state == "prepared":
            expired_by_time = self._now() >= _parse_timestamp(prepared["expires_at"])
            height_response = self.rpc.call("getBlockHeight", [{"commitment": "confirmed"}])
            expired_by_height = _result_int(height_response, "getBlockHeight") > row["last_valid_block_height"]
            if expired_by_time or expired_by_height:
                state = "expired"
                self.store.expire(row["execution_request_id"])
        return {
            "type": "external_execution_status",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "state": state,
            "updated_at": _now_text(self._now()),
        }

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        status = self.status(payload)
        expired = status["state"] == "expired"
        return {
            "type": "recovery_result",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "outcome": "failed_before_broadcast",
            "may_rematerialize": expired,
            "observed_at": _now_text(self._now()),
        }

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        return {
            "type": "preparation_evidence",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "prepared_execution": json.loads(row["prepared_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "observed_at": _now_text(self._now()),
        }

    def _required_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"execution_request_id"}:
            raise GatewayError("invalid_payload", "command requires only execution_request_id")
        request_id = payload["execution_request_id"]
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            raise GatewayError("invalid_payload", "execution_request_id is not canonical")
        row = self.store.get(request_id)
        if row is None:
            raise GatewayError("execution_not_found", "prepared execution was not found")
        return row

    def _validate_chain_accounts(
        self,
        *,
        mint: Pubkey,
        decimals: int,
        source_owner: Pubkey,
        destination_owner: Pubkey,
        source_ata: Pubkey,
        destination_ata: Pubkey,
        amount: int,
    ) -> None:
        mint_info = _account_info(self.rpc, mint)
        if mint_info["owner"] != TOKEN_PROGRAM:
            raise GatewayError("local_policy_block", "mint is not owned by the SPL Token program")
        parsed_mint = _parsed_info(mint_info, "mint")
        if parsed_mint.get("decimals") != decimals:
            raise GatewayError("local_policy_block", "declared mint decimals do not match chain state")
        for label, account, owner in (
            ("source", source_ata, source_owner),
            ("destination", destination_ata, destination_owner),
        ):
            info = _account_info(self.rpc, account)
            if info["owner"] != TOKEN_PROGRAM:
                raise GatewayError("local_policy_block", f"{label} token account has invalid owner program")
            parsed = _parsed_info(info, f"{label} token account")
            if parsed.get("mint") != str(mint) or parsed.get("owner") != str(owner):
                raise GatewayError("local_policy_block", f"{label} token account does not match the plan")
            if label == "source":
                token_amount = _object(parsed.get("tokenAmount"), "source token amount")
                if int(_string(token_amount.get("amount"), "source amount")) < amount:
                    raise GatewayError("local_policy_block", "source token balance is insufficient")


def _validate_prepare_payload(payload: dict[str, Any], now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(payload) != {"request", "preparation_context"}:
        raise GatewayError("invalid_payload", "prepare requires request and preparation_context")
    request = _object(payload["request"], "request")
    context = _object(payload["preparation_context"], "preparation_context")
    required_request = {
        "type",
        "protocol_version",
        "execution_request_id",
        "idempotency_key",
        "economic_plan",
        "economic_plan_hash",
        "economic_approval",
    }
    if set(request) != required_request:
        raise GatewayError("invalid_request", "external execution request is not closed")
    if request["type"] != "external_execution_request" or request["protocol_version"] != "1.0.0":
        raise GatewayError("invalid_request", "unsupported external execution request")
    for key in ("execution_request_id", "idempotency_key"):
        if not isinstance(request[key], str) or not _IDENTIFIER.fullmatch(request[key]):
            raise GatewayError("invalid_request", f"{key} is not canonical")
    plan = _object(request["economic_plan"], "economic_plan")
    required_plan = {
        "protocol_version",
        "normalization_profile",
        "obligation_id",
        "network",
        "capability",
        "asset",
        "amount_base_units",
        "source",
        "destination",
        "expires_at",
    }
    allowed_plan = required_plan | {"reason"}
    if not required_plan.issubset(plan) or not set(plan).issubset(allowed_plan):
        raise GatewayError("invalid_request", "economic plan is not closed")
    if (
        plan["protocol_version"] != "1.0.0"
        or plan["normalization_profile"] != "foundry-pay-domain-v1"
        or plan["network"] != "solana:devnet"
        or plan["capability"] != "solana.spl_transfer.v1"
    ):
        raise GatewayError("local_policy_block", "network or capability is not permitted")
    if not isinstance(plan["obligation_id"], str) or not _IDENTIFIER.fullmatch(plan["obligation_id"]):
        raise GatewayError("invalid_request", "obligation_id is not canonical")
    asset = _object(plan["asset"], "asset")
    if set(asset) != {"kind", "mint", "decimals"} or asset["kind"] != "spl-token":
        raise GatewayError("invalid_request", "asset is not a closed SPL token object")
    if (
        not isinstance(asset["decimals"], int)
        or isinstance(asset["decimals"], bool)
        or not 0 <= asset["decimals"] <= 18
    ):
        raise GatewayError("invalid_request", "asset decimals are invalid")
    if "reason" in plan and (not isinstance(plan["reason"], str) or not 1 <= len(plan["reason"]) <= 256):
        raise GatewayError("invalid_request", "reason must contain 1 to 256 characters")
    _pubkey(asset["mint"], "mint")
    _pubkey(plan["source"], "source")
    _pubkey(plan["destination"], "destination")
    if not isinstance(plan["amount_base_units"], str) or not _AMOUNT.fullmatch(plan["amount_base_units"]):
        raise GatewayError("invalid_request", "amount_base_units must be a canonical decimal string")
    if int(plan["amount_base_units"]) > 2**64 - 1:
        raise GatewayError("invalid_request", "amount_base_units exceeds SPL u64")
    if _parse_timestamp(plan["expires_at"]) <= now:
        raise GatewayError("request_expired", "economic plan has expired")
    expected_hash = _hash_json(plan)
    if request["economic_plan_hash"] != expected_hash:
        raise GatewayError("economic_plan_hash_mismatch", "economic plan hash does not match")
    approval = _object(request["economic_approval"], "economic approval")
    if set(approval) != {
        "approval_id",
        "economic_plan_hash",
        "approved_by",
        "issued_at",
        "expires_at",
    }:
        raise GatewayError("invalid_request", "economic approval is not closed")
    for key in ("approval_id", "approved_by"):
        if not isinstance(approval[key], str) or not _IDENTIFIER.fullmatch(approval[key]):
            raise GatewayError("invalid_request", f"economic approval {key} is not canonical")
    issued_at = _parse_timestamp(approval["issued_at"])
    if approval.get("economic_plan_hash") != expected_hash:
        raise GatewayError("local_policy_block", "economic approval is not bound to the plan")
    approval_expiry = _parse_timestamp(_string(approval.get("expires_at"), "approval expires_at"))
    if issued_at > now or approval_expiry <= issued_at:
        raise GatewayError("invalid_request", "economic approval time bounds are invalid")
    if approval_expiry <= now:
        raise GatewayError("approval_expired", "economic approval has expired")
    if set(context) != {"constraints"}:
        raise GatewayError("invalid_payload", "preparation_context is not closed")
    constraints = _object(context["constraints"], "constraints")
    if set(constraints) != {"max_fee_lamports", "allowed_programs"}:
        raise GatewayError("invalid_payload", "constraints are not closed")
    max_fee = constraints["max_fee_lamports"]
    programs = constraints["allowed_programs"]
    if not isinstance(max_fee, int) or isinstance(max_fee, bool) or not 0 <= max_fee <= 2**53 - 1:
        raise GatewayError("invalid_payload", "max_fee_lamports is invalid")
    if not isinstance(programs, list) or not programs:
        raise GatewayError("invalid_payload", "allowed_programs must be a non-empty array")
    for program in programs:
        _pubkey(program, "allowed program")
    return request, context


def _account_info(rpc: PreparationRpc, pubkey: Pubkey) -> dict[str, Any]:
    result = _result_object(
        rpc.call(
            "getAccountInfo",
            [str(pubkey), {"commitment": "confirmed", "encoding": "jsonParsed"}],
        ),
        "getAccountInfo",
    )
    value = result.get("value")
    if value is None:
        raise GatewayError("local_policy_block", f"required account does not exist: {pubkey}")
    return _object(value, "account info")


def _parsed_info(account: dict[str, Any], label: str) -> dict[str, Any]:
    data = _object(account.get("data"), f"{label} data")
    parsed = _object(data.get("parsed"), f"{label} parsed data")
    return _object(parsed.get("info"), f"{label} parsed info")


def _ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(Pubkey.from_string(TOKEN_PROGRAM)), bytes(mint)],
        Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM),
    )[0]


def _pubkey(value: Any, label: str) -> Pubkey:
    if not isinstance(value, str):
        raise GatewayError("invalid_request", f"{label} must be a Solana public key")
    try:
        key = Pubkey.from_string(value)
    except ValueError as error:
        raise GatewayError("invalid_request", f"{label} must be a canonical Solana public key") from error
    if str(key) != value:
        raise GatewayError("invalid_request", f"{label} must use canonical base58")
    return key


def _hash_json(value: Any) -> str:
    return _hash_bytes(rfc8785.dumps(value))


def _hash_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayError("invalid_payload", f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GatewayError("invalid_rpc_response", f"{label} must be a string")
    return value


def _safe_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2**53 - 1:
        raise GatewayError("invalid_rpc_response", f"{label} must be a safe unsigned integer")
    return value


def _result_object(response: dict[str, Any], method: str) -> dict[str, Any]:
    return _object(response.get("result"), f"{method} result")


def _result_string(response: dict[str, Any], method: str) -> str:
    return _string(response.get("result"), f"{method} result")


def _result_int(response: dict[str, Any], method: str) -> int:
    return _safe_int(response.get("result"), f"{method} result")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise GatewayError("invalid_request", "timestamp must be UTC RFC 3339 with second precision")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise GatewayError("invalid_request", "timestamp is not a real UTC instant") from error


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_text(value: datetime | None = None) -> str:
    return _timestamp((value or datetime.now(UTC)).replace(microsecond=0))
