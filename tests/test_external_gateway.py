from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from gateway.cli import run_jsonl
from gateway.journal import GatewayJournal
from gateway.protocol import GatewayError, GatewayRequest
from gateway.service import ExternalExecutionGateway


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((command, payload))
        return {"handled_by": command, "payload": payload}

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record("prepare", payload)

    def status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record("status", payload)

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record("recover", payload)

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record("evidence", payload)


def envelope(
    request_id: str = "gw_test_001",
    command: str = "prepare",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "gateway_protocol_version": "1.0.0",
        "gateway_request_id": request_id,
        "command": command,
        "payload": payload if payload is not None else {"execution_request_id": "exec_1"},
    }


@pytest.fixture
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / "gateway.sqlite3"


def test_routes_all_supported_commands(journal_path: Path) -> None:
    backend = RecordingBackend()
    gateway = ExternalExecutionGateway(GatewayJournal(journal_path), backend)

    for command in ("prepare", "status", "recover", "evidence"):
        response = gateway.handle(envelope(f"gw_{command}", command))
        assert response["ok"] is True
        assert response["result"]["handled_by"] == command
        assert isinstance(response["journal_sequence"], int)

    assert [command for command, _ in backend.calls] == [
        "prepare",
        "status",
        "recover",
        "evidence",
    ]


def test_identical_request_replays_without_backend_dispatch(journal_path: Path) -> None:
    backend = RecordingBackend()
    gateway = ExternalExecutionGateway(GatewayJournal(journal_path), backend)
    request = envelope()

    first = gateway.handle(request)
    second = gateway.handle(request)

    assert first["ok"] is True
    assert first["replayed"] is False
    assert second["ok"] is True
    assert second["replayed"] is True
    assert len(backend.calls) == 1


def test_replay_survives_process_restart(journal_path: Path) -> None:
    first_backend = RecordingBackend()
    first_gateway = ExternalExecutionGateway(GatewayJournal(journal_path), first_backend)
    first_gateway.handle(envelope())

    second_backend = RecordingBackend()
    restarted = ExternalExecutionGateway(GatewayJournal(journal_path), second_backend)
    response = restarted.handle(envelope())

    assert response["replayed"] is True
    assert second_backend.calls == []


def test_same_id_with_different_input_is_idempotency_conflict(
    journal_path: Path,
) -> None:
    backend = RecordingBackend()
    gateway = ExternalExecutionGateway(GatewayJournal(journal_path), backend)
    gateway.handle(envelope(payload={"execution_request_id": "exec_1"}))

    response = gateway.handle(
        envelope(payload={"execution_request_id": "exec_tampered"})
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "idempotency_conflict"
    assert len(backend.calls) == 1


def test_unfinished_reservation_is_not_redispatched(journal_path: Path) -> None:
    journal = GatewayJournal(journal_path)
    request = GatewayRequest.parse(envelope())
    journal.reserve(request)
    backend = RecordingBackend()

    response = ExternalExecutionGateway(journal, backend).handle(envelope())

    assert response["ok"] is False
    assert response["error"]["code"] == "needs_recovery"
    assert backend.calls == []


def test_response_is_durable_when_handle_returns(journal_path: Path) -> None:
    journal = GatewayJournal(journal_path)
    response = ExternalExecutionGateway(journal, RecordingBackend()).handle(envelope())
    stored = GatewayJournal(journal_path).get("gw_test_001")

    assert stored is not None
    assert stored["state"] == "completed"
    assert json.loads(stored["response_json"]) == response


def test_backend_protocol_error_is_persisted(journal_path: Path) -> None:
    class RejectingBackend(RecordingBackend):
        def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise GatewayError("local_policy_block", "program is not allowed")

    journal = GatewayJournal(journal_path)
    gateway = ExternalExecutionGateway(journal, RejectingBackend())

    response = gateway.handle(envelope())
    replay = gateway.handle(envelope())

    assert response["ok"] is False
    assert response["error"]["code"] == "local_policy_block"
    assert replay["replayed"] is True
    assert journal.get("gw_test_001")["state"] == "failed"  # type: ignore[index]


def test_envelope_is_closed_and_versioned() -> None:
    with pytest.raises(GatewayError, match="invalid fields"):
        GatewayRequest.parse({**envelope(), "prompt": "send freely"})

    with pytest.raises(GatewayError, match="expected gateway_protocol_version"):
        GatewayRequest.parse(
            {**envelope(), "gateway_protocol_version": "99.0.0"}
        )


def test_jsonl_emits_one_response_per_nonempty_line_and_continues(
    journal_path: Path,
) -> None:
    backend = RecordingBackend()
    gateway = ExternalExecutionGateway(GatewayJournal(journal_path), backend)
    source = io.StringIO(
        json.dumps(envelope("gw_prepare", "prepare"))
        + "\n{not-json}\n\n"
        + json.dumps(envelope("gw_status", "status"))
        + "\n"
    )
    target = io.StringIO()

    exit_code = run_jsonl(source, target, gateway)
    responses = [json.loads(line) for line in target.getvalue().splitlines()]

    assert exit_code == 0
    assert len(responses) == 3
    assert responses[0]["ok"] is True
    assert responses[1]["error"]["code"] == "invalid_json"
    assert responses[2]["ok"] is True
    assert all(json.dumps(response) for response in responses)


def test_jsonl_rejects_non_json_numeric_constants(journal_path: Path) -> None:
    gateway = ExternalExecutionGateway(
        GatewayJournal(journal_path), RecordingBackend()
    )
    source = io.StringIO(
        '{"gateway_protocol_version":"1.0.0","gateway_request_id":"gw_nan",'
        '"command":"prepare","payload":{"amount":NaN}}\n'
    )
    target = io.StringIO()

    run_jsonl(source, target, gateway)
    response = json.loads(target.getvalue())

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_json"


def test_transport_hash_ignores_object_key_order_but_not_array_order() -> None:
    first = GatewayRequest.parse(
        envelope(payload={"nested": {"b": 2, "a": 1}, "accounts": ["A", "B"]})
    )
    reordered_object = GatewayRequest.parse(
        envelope(payload={"accounts": ["A", "B"], "nested": {"a": 1, "b": 2}})
    )
    reordered_array = GatewayRequest.parse(
        envelope(payload={"nested": {"a": 1, "b": 2}, "accounts": ["B", "A"]})
    )

    assert first.request_hash == reordered_object.request_hash
    assert first.request_hash != reordered_array.request_hash
