from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from solana_agent.execution import ExecutionRequest, ExecutionResult

from .solana_cli import CLUSTER_URLS, PUBLIC_KEY

ALLOWED_RPC_ENDPOINTS = frozenset(CLUSTER_URLS.values())


class RpcTransport(Protocol):
    def call(self, endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]: ...


class UrllibRpcTransport:
    def call(self, endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ConnectionError(f"Solana RPC request failed: {exc}") from exc
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Solana RPC response must be an object")
        return value


@dataclass(slots=True)
class SolanaRpcAdapter:
    endpoint: str
    transport: RpcTransport | None = None

    def __post_init__(self) -> None:
        if self.endpoint not in ALLOWED_RPC_ENDPOINTS:
            raise ValueError(f"RPC endpoint is not allowlisted: {self.endpoint}")
        if self.transport is None:
            self.transport = UrllibRpcTransport()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        method, parameters = self._rpc_call(request.operation, request.arguments)
        payload = {"jsonrpc": "2.0", "id": request.command_id, "method": method, "params": parameters}
        assert self.transport is not None
        response = self.transport.call(self.endpoint, payload, request.timeout_seconds)
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        error = response.get("error")
        return ExecutionResult(
            exit_code=1 if error is not None else 0,
            stdout=encoded,
            stderr=json.dumps(error, ensure_ascii=False) if error is not None else "",
            metadata={
                "adapter": "solana_rpc",
                "endpoint": self.endpoint,
                "method": method,
                "result": response.get("result"),
            },
        )

    def _rpc_call(self, operation: str, arguments: dict[str, Any]) -> tuple[str, list[Any]]:
        commitment = arguments.get("commitment", "confirmed")
        if commitment not in {"processed", "confirmed", "finalized"}:
            raise ValueError("unsupported RPC commitment")
        if operation == "get_health":
            return "getHealth", []
        if operation == "get_version":
            return "getVersion", []
        if operation == "get_balance":
            return "getBalance", [self._public_key(arguments.get("pubkey")), {"commitment": commitment}]
        if operation == "get_account_info":
            return "getAccountInfo", [
                self._public_key(arguments.get("pubkey")),
                {"commitment": commitment, "encoding": "base64"},
            ]
        if operation == "get_signature_statuses":
            signatures = arguments.get("signatures")
            if (
                not isinstance(signatures, list)
                or not signatures
                or not all(isinstance(item, str) and item for item in signatures)
            ):
                raise ValueError("get_signature_statuses requires signatures")
            return "getSignatureStatuses", [
                signatures,
                {"searchTransactionHistory": bool(arguments.get("search_transaction_history", True))},
            ]
        if operation == "get_transaction":
            signature = arguments.get("signature")
            if not isinstance(signature, str) or not signature:
                raise ValueError("get_transaction requires signature")
            return "getTransaction", [
                signature,
                {
                    "commitment": commitment,
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                },
            ]
        raise ValueError(f"unsupported Solana RPC operation: {operation}")

    @staticmethod
    def _public_key(value: Any) -> str:
        if not isinstance(value, str) or not PUBLIC_KEY.fullmatch(value):
            raise ValueError("RPC pubkey must be a base58 Solana public key")
        return value
