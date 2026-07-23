"""Closed JSONL envelopes for the external execution gateway."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "1.0.0"
SUPPORTED_COMMANDS = frozenset({"prepare", "status", "recover", "evidence"})
_ENVELOPE_KEYS = frozenset(
    {"gateway_protocol_version", "gateway_request_id", "command", "payload"}
)


class GatewayError(Exception):
    """Structured gateway failure safe to serialize across the process boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            value["details"] = self.details
        return value


@dataclass(frozen=True)
class GatewayRequest:
    gateway_protocol_version: str
    gateway_request_id: str
    command: str
    payload: dict[str, Any]

    @classmethod
    def parse(cls, value: Any) -> GatewayRequest:
        if not isinstance(value, dict):
            raise GatewayError("invalid_envelope", "request must be a JSON object")

        actual_keys = frozenset(value)
        if actual_keys != _ENVELOPE_KEYS:
            missing = sorted(_ENVELOPE_KEYS - actual_keys)
            unexpected = sorted(actual_keys - _ENVELOPE_KEYS)
            raise GatewayError(
                "invalid_envelope",
                "request envelope has invalid fields",
                details={"missing": missing, "unexpected": unexpected},
            )

        version = value["gateway_protocol_version"]
        if version != PROTOCOL_VERSION:
            raise GatewayError(
                "unsupported_protocol_version",
                f"expected gateway_protocol_version {PROTOCOL_VERSION}",
            )

        request_id = value["gateway_request_id"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise GatewayError(
                "invalid_gateway_request_id",
                "gateway_request_id must be a non-empty string",
            )
        if len(request_id) > 128:
            raise GatewayError(
                "invalid_gateway_request_id",
                "gateway_request_id must not exceed 128 characters",
            )

        command = value["command"]
        if command not in SUPPORTED_COMMANDS:
            raise GatewayError(
                "unsupported_command",
                "command must be one of prepare, status, recover, evidence",
            )

        payload = value["payload"]
        if not isinstance(payload, dict):
            raise GatewayError("invalid_payload", "payload must be a JSON object")
        try:
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise GatewayError(
                "invalid_payload",
                "payload must contain only finite JSON values",
            ) from error

        return cls(version, request_id, command, payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gateway_protocol_version": self.gateway_protocol_version,
            "gateway_request_id": self.gateway_request_id,
            "command": self.command,
            "payload": self.payload,
        }

    @property
    def request_hash(self) -> str:
        encoded = json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def response_envelope(
    *,
    request_id: str | None,
    command: str | None,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    journal_sequence: int | None = None,
    replayed: bool = False,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "gateway_protocol_version": PROTOCOL_VERSION,
        "gateway_request_id": request_id,
        "command": command,
        "ok": ok,
        "replayed": replayed,
    }
    if result is not None:
        response["result"] = result
    if error is not None:
        response["error"] = error
    if journal_sequence is not None:
        response["journal_sequence"] = journal_sequence
    return response
