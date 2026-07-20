from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from solana_agent.contracts.command import CommandRecord, CommandStatus, require_command_transition
from solana_agent.contracts.lifecycle import RunRecord, RunStatus

from .database import Database


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def load_json(value: str | None) -> Any:
    return json.loads(value) if value else None


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: str
    run_id: str
    command_id: str | None
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    run_id: str
    command_id: str | None
    kind: str
    content: str
    content_hash: str
    size_bytes: int
    created_at: str


class JournalRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_run(
        self,
        *,
        run_id: str,
        mission_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs(id, mission_id, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, mission_id, RunStatus.CREATED.value, canonical_json(metadata or {}), now, now),
            )
            self._append_event(
                connection,
                run_id=run_id,
                command_id=None,
                event_type="run.created",
                payload={"mission_id": mission_id},
                created_at=now,
            )
        return self.require_run(run_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row is not None else None

    def require_run(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        return run

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: dict[str, Any] | None = None,
    ) -> RunRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ?, error_json = ?, updated_at = ? WHERE id = ?",
                (status.value, canonical_json(error) if error else None, now, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"run not found: {run_id}")
            self._append_event(
                connection,
                run_id=run_id,
                command_id=None,
                event_type=f"run.{status.value}",
                payload={"error": error} if error else {},
                created_at=now,
            )
        return self.require_run(run_id)

    def insert_planned_command(
        self,
        *,
        command_id: str,
        run_id: str,
        step_id: str,
        adapter: str,
        operation: str,
        arguments: dict[str, Any],
        cwd: str,
        timeout_seconds: int,
        idempotency_key: str,
        expected_state: str | None,
    ) -> CommandRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO commands(
                    id, run_id, step_id, adapter, operation, arguments_json, cwd,
                    timeout_seconds, idempotency_key, status, expected_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    run_id,
                    step_id,
                    adapter,
                    operation,
                    canonical_json(arguments),
                    cwd,
                    timeout_seconds,
                    idempotency_key,
                    CommandStatus.PLANNED.value,
                    expected_state,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id=run_id,
                command_id=command_id,
                event_type="command.planned",
                payload={"adapter": adapter, "operation": operation, "step_id": step_id},
                created_at=now,
            )
        return self.require_command(command_id)

    def get_command(self, command_id: str) -> CommandRecord | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        return self._command_from_row(row) if row is not None else None

    def require_command(self, command_id: str) -> CommandRecord:
        command = self.get_command(command_id)
        if command is None:
            raise KeyError(f"command not found: {command_id}")
        return command

    def find_command_by_idempotency(self, run_id: str, idempotency_key: str) -> CommandRecord | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM commands WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
        return self._command_from_row(row) if row is not None else None

    def transition_command(
        self,
        command_id: str,
        target: CommandStatus,
        *,
        payload: dict[str, Any] | None = None,
        policy_decision: str | None = None,
        policy_reason: str | None = None,
        approval_id: str | None = None,
        exit_code: int | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        stdout_artifact_id: str | None = None,
        stderr_artifact_id: str | None = None,
    ) -> CommandRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if row is None:
                raise KeyError(f"command not found: {command_id}")
            current = CommandStatus(str(row["status"]))
            require_command_transition(current, target)
            started_at = now if target == CommandStatus.RUNNING else row["started_at"]
            finished_at = now if target in {
                CommandStatus.SUCCEEDED,
                CommandStatus.FAILED,
                CommandStatus.REJECTED,
                CommandStatus.CANCELLED,
                CommandStatus.INTERRUPTED,
                CommandStatus.TIMED_OUT,
            } else row["finished_at"]
            connection.execute(
                """
                UPDATE commands
                SET status = ?, policy_decision = COALESCE(?, policy_decision),
                    policy_reason = COALESCE(?, policy_reason), approval_id = COALESCE(?, approval_id),
                    started_at = ?, finished_at = ?, exit_code = COALESCE(?, exit_code),
                    result_json = COALESCE(?, result_json), error_json = COALESCE(?, error_json),
                    stdout_artifact_id = COALESCE(?, stdout_artifact_id),
                    stderr_artifact_id = COALESCE(?, stderr_artifact_id), updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    target.value,
                    policy_decision,
                    policy_reason,
                    approval_id,
                    started_at,
                    finished_at,
                    exit_code,
                    canonical_json(result) if result is not None else None,
                    canonical_json(error) if error is not None else None,
                    stdout_artifact_id,
                    stderr_artifact_id,
                    now,
                    command_id,
                    current.value,
                ),
            )
            event_payload = dict(payload or {})
            if policy_decision is not None:
                event_payload["policy_decision"] = policy_decision
            if policy_reason is not None:
                event_payload["policy_reason"] = policy_reason
            if exit_code is not None:
                event_payload["exit_code"] = exit_code
            if error is not None:
                event_payload["error"] = error
            self._append_event(
                connection,
                run_id=str(row["run_id"]),
                command_id=command_id,
                event_type=f"command.{target.value}",
                payload=event_payload,
                created_at=now,
            )
        return self.require_command(command_id)

    def create_artifact(
        self,
        *,
        run_id: str,
        command_id: str | None,
        kind: str,
        content: str,
    ) -> ArtifactRecord:
        artifact_id = f"artifact-{uuid.uuid4().hex}"
        encoded = content.encode("utf-8")
        content_hash = hashlib.sha256(encoded).hexdigest()
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(id, run_id, command_id, kind, content_text, content_hash, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, run_id, command_id, kind, content, content_hash, len(encoded), now),
            )
            self._append_event(
                connection,
                run_id=run_id,
                command_id=command_id,
                event_type="artifact.created",
                payload={"artifact_id": artifact_id, "kind": kind, "content_hash": content_hash},
                created_at=now,
            )
        return self.require_artifact(artifact_id)

    def require_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        return self._artifact_from_row(row)

    def list_commands(self, run_id: str) -> list[CommandRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM commands WHERE run_id = ? ORDER BY created_at, id", (run_id,)
            ).fetchall()
        return [self._command_from_row(row) for row in rows]

    def list_events(self, run_id: str) -> list[EventRecord]:
        with self.database.read() as connection:
            rows = connection.execute("SELECT * FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_artifacts(self, command_id: str) -> list[ArtifactRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE command_id = ? ORDER BY created_at, id", (command_id,)
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def list_running_commands(self) -> list[CommandRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM commands WHERE status = ? ORDER BY created_at",
                (CommandStatus.RUNNING.value,),
            ).fetchall()
        return [self._command_from_row(row) for row in rows]

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        command_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO events(id, run_id, command_id, sequence, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"event-{uuid.uuid4().hex}",
                run_id,
                command_id,
                sequence,
                event_type,
                canonical_json(payload),
                created_at,
            ),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=str(row["id"]),
            mission_id=str(row["mission_id"]),
            status=RunStatus(str(row["status"])),
            metadata=load_json(row["metadata_json"]) or {},
            error=load_json(row["error_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _command_from_row(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            step_id=str(row["step_id"]),
            adapter=str(row["adapter"]),
            operation=str(row["operation"]),
            arguments=load_json(row["arguments_json"]) or {},
            cwd=str(row["cwd"]),
            timeout_seconds=int(row["timeout_seconds"]),
            idempotency_key=str(row["idempotency_key"]),
            status=CommandStatus(str(row["status"])),
            expected_state=str(row["expected_state"]) if row["expected_state"] is not None else None,
            policy_decision=str(row["policy_decision"]) if row["policy_decision"] is not None else None,
            policy_reason=str(row["policy_reason"]) if row["policy_reason"] is not None else None,
            approval_id=str(row["approval_id"]) if row["approval_id"] is not None else None,
            started_at=str(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
            result=load_json(row["result_json"]),
            error=load_json(row["error_json"]),
            stdout_artifact_id=(
                str(row["stdout_artifact_id"]) if row["stdout_artifact_id"] is not None else None
            ),
            stderr_artifact_id=(
                str(row["stderr_artifact_id"]) if row["stderr_artifact_id"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            command_id=str(row["command_id"]) if row["command_id"] is not None else None,
            sequence=int(row["sequence"]),
            event_type=str(row["event_type"]),
            payload=load_json(row["payload_json"]) or {},
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            command_id=str(row["command_id"]) if row["command_id"] is not None else None,
            kind=str(row["kind"]),
            content=str(row["content_text"]),
            content_hash=str(row["content_hash"]),
            size_bytes=int(row["size_bytes"]),
            created_at=str(row["created_at"]),
        )
