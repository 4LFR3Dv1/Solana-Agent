"""Authorized single-broadcast execution layered over the preparation backend."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import rfc8785
from solders.message import from_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from gateway.chaos import ChaosHook, reach
from gateway.protocol import GatewayError
from gateway.solana_prepare import (
    PreparationRpc,
    RpcDefinitiveRejection,
    SolanaPreparationBackend,
    _hash_bytes,
    _now_text,
    _parse_timestamp,
    _pubkey,
    _result_int,
    _result_object,
    _result_string,
)

_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUTHORIZATION_FIELDS = {
    "type",
    "protocol_version",
    "authorization_id",
    "execution_request_id",
    "execution_commitment_hash",
    "prepared_message_hash",
    "signer",
    "single_use",
    "issued_at",
    "expires_at",
    "authorization_signature",
}
_SIGNATURE_FIELDS = {"signer", "signature"}


class AuthorizationVerifier(Protocol):
    """Verifies Foundry authorization authenticity with public material only."""

    def verify(self, payload: bytes, signature: str) -> bool: ...


class Ed25519AuthorizationVerifier:
    """Foundry authorization verifier backed by a configured Ed25519 public key."""

    def __init__(self, authority: str) -> None:
        self.authority = _pubkey(authority, "Foundry authorization authority")

    def verify(self, payload: bytes, signature: str) -> bool:
        try:
            parsed = Signature.from_string(signature)
        except ValueError:
            return False
        return parsed.verify(self.authority, payload)


class ExecutionStore:
    """Durable signature-first journal for a single blockchain submission."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_attempts (
                    execution_request_id TEXT PRIMARY KEY,
                    authorization_id TEXT NOT NULL UNIQUE,
                    prepared_message_hash TEXT NOT NULL,
                    execution_commitment_hash TEXT NOT NULL,
                    signer TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    signed_transaction_base64 TEXT NOT NULL,
                    authorization_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    signature_persisted_at TEXT NOT NULL,
                    submitted_at TEXT,
                    confirmed_at TEXT,
                    rpc_submission_json TEXT,
                    status_observation_json TEXT,
                    receipt_json TEXT,
                    broadcast_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(execution_attempts)").fetchall()}
            if "broadcast_count" not in columns:
                connection.execute(
                    """
                    ALTER TABLE execution_attempts
                    ADD COLUMN broadcast_count INTEGER NOT NULL DEFAULT 1
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def claim(
        self,
        *,
        execution_request_id: str,
        authorization_id: str,
        prepared_message_hash: str,
        execution_commitment_hash: str,
        signer: str,
        signature: str,
        signed_transaction_base64: str,
        authorization: Mapping[str, Any],
        now: datetime,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM execution_attempts
                WHERE execution_request_id = ? OR authorization_id = ?
                LIMIT 1
                """,
                (execution_request_id, authorization_id),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["execution_request_id"] == execution_request_id
                    and existing["authorization_id"] == authorization_id
                    and existing["prepared_message_hash"] == prepared_message_hash
                    and existing["execution_commitment_hash"] == execution_commitment_hash
                    and existing["signature"] == signature
                )
                if not same:
                    raise GatewayError(
                        "execution_conflict",
                        "request or authorization is bound to another execution attempt",
                    )
                if existing["state"] in {"broadcast_started", "needs_recovery"}:
                    raise GatewayError(
                        "needs_recovery",
                        "a prior broadcast outcome is unknown; automatic rebroadcast is forbidden",
                        details={"signature": existing["signature"]},
                    )
                raise GatewayError(
                    "execution_already_started",
                    "authorization has already been consumed by an execution attempt",
                    details={
                        "state": existing["state"],
                        "signature": existing["signature"],
                    },
                )
            timestamp = _now_text(now)
            connection.execute(
                """
                INSERT INTO execution_attempts (
                    execution_request_id,
                    authorization_id,
                    prepared_message_hash,
                    execution_commitment_hash,
                    signer,
                    signature,
                    signed_transaction_base64,
                    authorization_json,
                    state,
                    broadcast_count,
                    signature_persisted_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'broadcast_started', 1, ?, ?)
                """,
                (
                    execution_request_id,
                    authorization_id,
                    prepared_message_hash,
                    execution_commitment_hash,
                    signer,
                    signature,
                    signed_transaction_base64,
                    _json(authorization),
                    timestamp,
                    timestamp,
                ),
            )

    def record_submitted(
        self,
        execution_request_id: str,
        *,
        rpc_submission: Mapping[str, Any],
        receipt: Mapping[str, Any],
        now: datetime,
    ) -> None:
        timestamp = _now_text(now)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """
                UPDATE execution_attempts
                SET state = 'submitted',
                    submitted_at = ?,
                    rpc_submission_json = ?,
                    receipt_json = ?,
                    updated_at = ?
                WHERE execution_request_id = ? AND state = 'broadcast_started'
                """,
                (
                    timestamp,
                    _json(rpc_submission),
                    _json(receipt),
                    timestamp,
                    execution_request_id,
                ),
            )
            if result.rowcount != 1:
                raise GatewayError(
                    "needs_recovery",
                    "submission response could not be attached to the durable signature",
                )

    def mark_needs_recovery(self, execution_request_id: str, *, now: datetime) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE execution_attempts
                SET state = 'needs_recovery', updated_at = ?
                WHERE execution_request_id = ? AND state = 'broadcast_started'
                """,
                (_now_text(now), execution_request_id),
            )

    def record_definitive_rejection(
        self,
        execution_request_id: str,
        *,
        error: GatewayError,
        now: datetime,
    ) -> None:
        timestamp = _now_text(now)
        observation = {
            "outcome": "definitive_rejection",
            "error": error.as_dict(),
        }
        with closing(self._connect()) as connection, connection:
            result = connection.execute(
                """
                UPDATE execution_attempts
                SET state = 'failed',
                    status_observation_json = ?,
                    updated_at = ?
                WHERE execution_request_id = ? AND state = 'broadcast_started'
                """,
                (_json(observation), timestamp, execution_request_id),
            )
            if result.rowcount != 1:
                raise GatewayError(
                    "needs_recovery",
                    "definitive rejection could not be attached to the durable attempt",
                )

    def record_observation(
        self,
        execution_request_id: str,
        *,
        state: str,
        observation: Mapping[str, Any],
        receipt: Mapping[str, Any] | None,
        now: datetime,
    ) -> None:
        timestamp = _now_text(now)
        confirmed_at = timestamp if state == "confirmed" else None
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE execution_attempts
                SET state = ?,
                    status_observation_json = ?,
                    receipt_json = COALESCE(?, receipt_json),
                    confirmed_at = COALESCE(?, confirmed_at),
                    updated_at = ?
                WHERE execution_request_id = ?
                """,
                (
                    state,
                    _json(observation),
                    _json(receipt) if receipt is not None else None,
                    confirmed_at,
                    timestamp,
                    execution_request_id,
                ),
            )

    def get(self, execution_request_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM execution_attempts WHERE execution_request_id = ?",
                (execution_request_id,),
            ).fetchone()
        return dict(row) if row is not None else None


class SolanaExecutionBackend(SolanaPreparationBackend):
    """PREPARE plus exact authorized signature submission and recovery."""

    def __init__(
        self,
        *,
        journal_path: Path,
        signer: str,
        authorization_verifier: AuthorizationVerifier | None,
        rpc: PreparationRpc | None = None,
        executor_id: str = "solana-agent",
        now: Callable[[], datetime] | None = None,
        chaos_hook: ChaosHook | None = None,
    ) -> None:
        super().__init__(
            journal_path=journal_path,
            signer=signer,
            rpc=rpc,
            executor_id=executor_id,
            now=now,
        )
        self.authorization_verifier = authorization_verifier
        self.executions = ExecutionStore(journal_path)
        self.chaos_hook = chaos_hook

    def authorize_and_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.authorization_verifier is None:
            raise GatewayError(
                "authorization_verifier_not_configured",
                "Foundry authorization verification is required for execution",
            )
        prepared, authorization, message, signature = self._validate_execution(payload)
        execution_request_id = prepared["execution_request_id"]
        now = self._now().replace(microsecond=0)

        versioned_message = from_bytes_versioned(message)
        if versioned_message.header.num_required_signatures != 1:
            raise GatewayError(
                "local_policy_block",
                "prepared message must require exactly one signature",
            )
        if versioned_message.account_keys[0] != self.signer:
            raise GatewayError(
                "local_policy_block",
                "prepared message fee payer does not match configured signer",
            )
        if not signature.verify(self.signer, message):
            raise GatewayError(
                "message_signature_invalid",
                "Solana signature does not verify over the exact prepared message",
            )
        signed_transaction = VersionedTransaction.populate(versioned_message, [signature])
        signed_transaction_base64 = base64.b64encode(bytes(signed_transaction)).decode("ascii")

        reach(
            self.chaos_hook,
            "after_execution_validated_before_claim",
            {
                "execution_request_id": execution_request_id,
                "signature": str(signature),
            },
        )
        self.executions.claim(
            execution_request_id=execution_request_id,
            authorization_id=authorization["authorization_id"],
            prepared_message_hash=prepared["prepared_message_hash"],
            execution_commitment_hash=prepared["execution_commitment_hash"],
            signer=str(self.signer),
            signature=str(signature),
            signed_transaction_base64=signed_transaction_base64,
            authorization=authorization,
            now=now,
        )
        reach(
            self.chaos_hook,
            "after_signature_and_broadcast_intent_persisted",
            {
                "execution_request_id": execution_request_id,
                "signature": str(signature),
            },
        )

        try:
            reach(
                self.chaos_hook,
                "before_send_transaction",
                {
                    "execution_request_id": execution_request_id,
                    "signature": str(signature),
                },
            )
            rpc_response = self.rpc.call(
                "sendTransaction",
                [
                    signed_transaction_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed",
                        "maxRetries": 0,
                    },
                ],
            )
            returned_signature = _result_string(rpc_response, "sendTransaction")
            if returned_signature != str(signature):
                raise GatewayError(
                    "rpc_signature_mismatch",
                    "RPC returned a signature different from the persisted transaction signature",
                )
            reach(
                self.chaos_hook,
                "after_send_transaction_response_before_persist",
                {
                    "execution_request_id": execution_request_id,
                    "signature": str(signature),
                },
            )
            receipt = self._receipt(
                prepared,
                authorization,
                signature=str(signature),
                state="submitted",
                observed_at=now,
            )
            self.executions.record_submitted(
                execution_request_id,
                rpc_submission=rpc_response,
                receipt=receipt,
                now=now,
            )
            return receipt
        except RpcDefinitiveRejection as error:
            self.executions.record_definitive_rejection(
                execution_request_id,
                error=error,
                now=now,
            )
            raise
        except Exception as error:
            self.executions.mark_needs_recovery(execution_request_id, now=now)
            raise GatewayError(
                "needs_recovery",
                "broadcast may have occurred; consult status or recover and never rebroadcast",
                retryable=False,
                details={
                    "execution_request_id": execution_request_id,
                    "signature": str(signature),
                },
            ) from error

    def status(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        attempt = self.executions.get(row["execution_request_id"])
        if attempt is None:
            return super().status(payload)
        refreshed = self._refresh_attempt(row, attempt)
        return {
            "type": "external_execution_status",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "state": refreshed["state"],
            "signature": refreshed["signature"],
            "updated_at": refreshed["updated_at"],
        }

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        attempt = self.executions.get(row["execution_request_id"])
        if attempt is None:
            return super().recover(payload)
        refreshed = self._refresh_attempt(row, attempt)
        state = refreshed["state"]
        if state == "confirmed":
            outcome = "recovered_confirmed"
            may_rematerialize = False
        elif state == "failed":
            outcome = "recovered_failed"
            may_rematerialize = True
        else:
            observation = (
                json.loads(refreshed["status_observation_json"]) if refreshed["status_observation_json"] else None
            )
            if observation is not None and observation.get("value") is not None:
                outcome = "submitted"
                may_rematerialize = False
            else:
                current_height = _result_int(
                    self.rpc.call("getBlockHeight", [{"commitment": "confirmed"}]),
                    "getBlockHeight",
                )
                if current_height > row["last_valid_block_height"]:
                    expiry_observation = {
                        "signature": refreshed["signature"],
                        "value": None,
                        "block_height": current_height,
                        "last_valid_block_height": row["last_valid_block_height"],
                        "reason": "signature_not_found_after_blockhash_expiry",
                    }
                    self.executions.record_observation(
                        row["execution_request_id"],
                        state="needs_recovery",
                        observation=expiry_observation,
                        receipt=None,
                        now=self._now(),
                    )
                    outcome = "not_found_after_expiry_needs_reconciliation"
                    state = "needs_recovery"
                else:
                    outcome = "needs_recovery"
                may_rematerialize = False
        return {
            "type": "recovery_result",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "outcome": outcome,
            "state": state,
            "signature": refreshed["signature"],
            "may_rematerialize": may_rematerialize,
            "observed_at": _now_text(self._now()),
        }

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._required_row(payload)
        preparation = super().evidence(payload)
        attempt = self.executions.get(row["execution_request_id"])
        if attempt is None:
            return preparation
        return {
            "type": "execution_evidence",
            "protocol_version": "1.0.0",
            "execution_request_id": row["execution_request_id"],
            "prepared_execution": preparation["prepared_execution"],
            "preparation_evidence": preparation["evidence"],
            "execution": _public_attempt(attempt),
            "observed_at": _now_text(self._now()),
        }

    def _validate_execution(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], bytes, Signature]:
        if set(payload) != {
            "execution_request_id",
            "prepared_message_base64",
            "execution_authorization",
            "message_signature",
        }:
            raise GatewayError(
                "invalid_payload",
                "authorize-and-execute payload is not closed",
            )
        execution_request_id = _identifier(
            payload["execution_request_id"],
            "execution_request_id",
        )
        row = self.store.get(execution_request_id)
        if row is None:
            raise GatewayError("execution_not_found", "prepared execution was not found")
        if row["state"] != "prepared":
            raise GatewayError(
                "execution_not_prepared",
                "only an active prepared execution can be authorized",
            )
        prepared = json.loads(row["prepared_json"])
        now = self._now().replace(microsecond=0)
        if _parse_timestamp(prepared["expires_at"]) <= now:
            self.store.expire(execution_request_id)
            raise GatewayError("prepared_execution_expired", "prepared execution has expired")
        current_height = _result_int(
            self.rpc.call("getBlockHeight", [{"commitment": "confirmed"}]),
            "getBlockHeight",
        )
        if current_height > row["last_valid_block_height"]:
            self.store.expire(execution_request_id)
            raise GatewayError("prepared_execution_expired", "prepared blockhash has expired")

        supplied_base64 = payload["prepared_message_base64"]
        if not isinstance(supplied_base64, str) or supplied_base64 != prepared["prepared_message_base64"]:
            raise GatewayError(
                "prepared_message_mismatch",
                "supplied prepared message differs from the persisted preparation",
            )
        message = _decode_canonical_base64(supplied_base64)
        if _hash_bytes(message) != prepared["prepared_message_hash"]:
            raise GatewayError(
                "prepared_message_hash_mismatch",
                "exact prepared message hash does not match persisted preparation",
            )

        authorization = _closed_object(
            payload["execution_authorization"],
            _AUTHORIZATION_FIELDS,
            "execution_authorization",
        )
        if (
            authorization["type"] != "execution_authorization"
            or authorization["protocol_version"] != "1.0.0"
            or authorization["single_use"] is not True
        ):
            raise GatewayError("authorization_invalid", "unsupported execution authorization")
        _identifier(authorization["authorization_id"], "authorization_id")
        for field in ("execution_commitment_hash", "prepared_message_hash"):
            _digest(authorization[field], field)
        if (
            authorization["execution_request_id"] != execution_request_id
            or authorization["execution_commitment_hash"] != prepared["execution_commitment_hash"]
            or authorization["prepared_message_hash"] != prepared["prepared_message_hash"]
            or authorization["signer"] != prepared["signer"]
        ):
            raise GatewayError(
                "authorization_binding_mismatch",
                "authorization is not bound to the persisted prepared execution",
            )
        issued_at = _parse_timestamp(authorization["issued_at"])
        authorization_expiry = _parse_timestamp(authorization["expires_at"])
        if issued_at > now or authorization_expiry <= issued_at:
            raise GatewayError("authorization_invalid", "authorization time bounds are invalid")
        if authorization_expiry <= now:
            raise GatewayError("authorization_expired", "execution authorization has expired")
        if authorization_expiry > _parse_timestamp(prepared["expires_at"]):
            raise GatewayError(
                "authorization_invalid",
                "execution authorization outlives the prepared execution",
            )
        authorization_signature = authorization["authorization_signature"]
        if not isinstance(authorization_signature, str) or not authorization_signature:
            raise GatewayError("authorization_invalid", "authorization signature is missing")
        unsigned_authorization = {
            key: value for key, value in authorization.items() if key != "authorization_signature"
        }
        assert self.authorization_verifier is not None
        try:
            authentic = self.authorization_verifier.verify(
                rfc8785.dumps(unsigned_authorization),
                authorization_signature,
            )
        except Exception as error:
            raise GatewayError(
                "authorization_invalid",
                "authorization signature verification failed",
            ) from error
        if authentic is not True:
            raise GatewayError(
                "authorization_invalid",
                "authorization signature verification failed",
            )

        message_signature = _closed_object(
            payload["message_signature"],
            _SIGNATURE_FIELDS,
            "message_signature",
        )
        if message_signature["signer"] != prepared["signer"]:
            raise GatewayError(
                "message_signature_invalid",
                "message signature signer does not match the preparation",
            )
        signature_text = message_signature["signature"]
        if not isinstance(signature_text, str):
            raise GatewayError("message_signature_invalid", "signature must be base58")
        try:
            signature = Signature.from_string(signature_text)
        except ValueError as error:
            raise GatewayError(
                "message_signature_invalid",
                "signature must be canonical base58",
            ) from error
        if str(signature) != signature_text:
            raise GatewayError(
                "message_signature_invalid",
                "signature must use canonical base58",
            )
        return prepared, authorization, message, signature

    def _refresh_attempt(
        self,
        preparation_row: dict[str, Any],
        attempt: dict[str, Any],
    ) -> dict[str, Any]:
        if attempt["state"] in {"confirmed", "failed"}:
            return attempt
        response = self.rpc.call(
            "getSignatureStatuses",
            [[attempt["signature"]], {"searchTransactionHistory": True}],
        )
        result = _result_object(response, "getSignatureStatuses")
        values = result.get("value")
        if not isinstance(values, list) or len(values) != 1:
            raise GatewayError(
                "invalid_rpc_response",
                "getSignatureStatuses must return exactly one value",
            )
        value = values[0]
        if value is None:
            state = "submitted" if attempt["state"] == "submitted" else "needs_recovery"
            observation: dict[str, Any] = {
                "signature": attempt["signature"],
                "value": None,
            }
            receipt = None
        elif isinstance(value, dict):
            observation = {
                "signature": attempt["signature"],
                "value": value,
            }
            if value.get("err") is not None:
                state = "failed"
                receipt = self._receipt_from_attempt(
                    preparation_row,
                    attempt,
                    state=state,
                    observation=observation,
                )
            elif value.get("confirmationStatus") in {"confirmed", "finalized"}:
                state = "confirmed"
                receipt = self._receipt_from_attempt(
                    preparation_row,
                    attempt,
                    state=state,
                    observation=observation,
                )
            else:
                state = "submitted"
                receipt = None
        else:
            raise GatewayError(
                "invalid_rpc_response",
                "signature status value must be an object or null",
            )
        self.executions.record_observation(
            preparation_row["execution_request_id"],
            state=state,
            observation=observation,
            receipt=receipt,
            now=self._now(),
        )
        refreshed = self.executions.get(preparation_row["execution_request_id"])
        assert refreshed is not None
        return refreshed

    def _receipt(
        self,
        prepared: Mapping[str, Any],
        authorization: Mapping[str, Any],
        *,
        signature: str,
        state: str,
        observed_at: datetime,
        observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "type": "external_execution_receipt",
            "protocol_version": "1.0.0",
            "execution_request_id": prepared["execution_request_id"],
            "authorization_id": authorization["authorization_id"],
            "execution_commitment_hash": prepared["execution_commitment_hash"],
            "prepared_message_hash": prepared["prepared_message_hash"],
            "signer": prepared["signer"],
            "signature": signature,
            "state": state,
            "observed_at": _now_text(observed_at),
        }
        if observation is not None:
            receipt["chain_observation"] = dict(observation)
        return receipt

    def _receipt_from_attempt(
        self,
        preparation_row: dict[str, Any],
        attempt: dict[str, Any],
        *,
        state: str,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._receipt(
            json.loads(preparation_row["prepared_json"]),
            json.loads(attempt["authorization_json"]),
            signature=attempt["signature"],
            state=state,
            observed_at=self._now(),
            observation=observation,
        )


def _closed_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GatewayError("invalid_payload", f"{label} is not a closed object")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise GatewayError("invalid_payload", f"{label} is not canonical")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise GatewayError("invalid_payload", f"{label} is not a canonical SHA-256 digest")
    return value


def _decode_canonical_base64(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as error:
        raise GatewayError("invalid_payload", "prepared message is not canonical base64") from error
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        raise GatewayError("invalid_payload", "prepared message is not canonical base64")
    return decoded


def _public_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authorization_id": attempt["authorization_id"],
        "prepared_message_hash": attempt["prepared_message_hash"],
        "execution_commitment_hash": attempt["execution_commitment_hash"],
        "signer": attempt["signer"],
        "signature": attempt["signature"],
        "signed_transaction_base64": attempt["signed_transaction_base64"],
        "authorization": json.loads(attempt["authorization_json"]),
        "state": attempt["state"],
        "signature_persisted_at": attempt["signature_persisted_at"],
        "broadcast_count": attempt["broadcast_count"],
        "submitted_at": attempt["submitted_at"],
        "confirmed_at": attempt["confirmed_at"],
        "rpc_submission": (json.loads(attempt["rpc_submission_json"]) if attempt["rpc_submission_json"] else None),
        "status_observation": (
            json.loads(attempt["status_observation_json"]) if attempt["status_observation_json"] else None
        ),
        "receipt": json.loads(attempt["receipt_json"]) if attempt["receipt_json"] else None,
        "updated_at": attempt["updated_at"],
    }


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
