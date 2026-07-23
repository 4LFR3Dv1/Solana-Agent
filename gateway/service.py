"""Command dispatch with durable idempotency around a pluggable backend."""

from __future__ import annotations

from typing import Any, Callable

from gateway.backend import ExternalExecutionBackend
from gateway.journal import GatewayJournal
from gateway.protocol import GatewayError, GatewayRequest, response_envelope


class ExternalExecutionGateway:
    def __init__(
        self,
        journal: GatewayJournal,
        backend: ExternalExecutionBackend,
    ) -> None:
        self.journal = journal
        self.backend = backend

    def handle(self, value: Any) -> dict[str, Any]:
        request = GatewayRequest.parse(value)
        reservation = self.journal.reserve(request)

        if reservation.response is not None:
            return reservation.response

        if reservation.disposition == "conflict":
            response = response_envelope(
                request_id=request.gateway_request_id,
                command=request.command,
                ok=False,
                error=GatewayError(
                    "idempotency_conflict",
                    "gateway_request_id was already used for different input",
                ).as_dict(),
            )
            return self.journal.record_response(
                request.gateway_request_id,
                "idempotency_conflict_response",
                response,
            )

        if reservation.disposition == "needs_recovery":
            response = response_envelope(
                request_id=request.gateway_request_id,
                command=request.command,
                ok=False,
                error=GatewayError(
                    "needs_recovery",
                    "a prior attempt has no durable response; automatic redispatch is forbidden",
                    retryable=False,
                ).as_dict(),
            )
            return self.journal.record_response(
                request.gateway_request_id,
                "recovery_required_response",
                response,
            )

        try:
            handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(
                self.backend, request.command
            )
            result = handler(request.payload)
            if not isinstance(result, dict):
                raise GatewayError(
                    "invalid_backend_response",
                    "backend response must be a JSON object",
                )
            response = response_envelope(
                request_id=request.gateway_request_id,
                command=request.command,
                ok=True,
                result=result,
            )
            return self.journal.complete(request, response, failed=False)
        except GatewayError as error:
            response = response_envelope(
                request_id=request.gateway_request_id,
                command=request.command,
                ok=False,
                error=error.as_dict(),
            )
            return self.journal.complete(request, response, failed=True)
        except Exception:
            response = response_envelope(
                request_id=request.gateway_request_id,
                command=request.command,
                ok=False,
                error=GatewayError(
                    "backend_failure",
                    "backend failed without a protocol-safe error",
                    retryable=False,
                ).as_dict(),
            )
            return self.journal.complete(request, response, failed=True)
