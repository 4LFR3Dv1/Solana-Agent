"""Backend boundary kept separate from the existing Solana-Agent kernel."""

from __future__ import annotations

from typing import Any, Protocol

from gateway.protocol import GatewayError


class ExternalExecutionBackend(Protocol):
    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def status(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class UnavailableExecutionBackend:
    """Fail-closed default until SA-GW-002 wires Solana preparation."""

    def _unavailable(self, command: str) -> dict[str, Any]:
        raise GatewayError(
            "backend_not_configured",
            f"{command} is not connected to a Solana execution backend",
            details={"next_work_item": "SA-GW-002"},
        )

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("prepare")

    def status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("status")

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("recover")

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("evidence")
