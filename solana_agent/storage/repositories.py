from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from solana_agent.contracts.authority import (
    ApprovalRecord,
    ApprovalStatus,
    PolicyDecision,
    PolicyDecisionRecord,
    PolicyEffect,
    RiskLevel,
)
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
        policy_snapshot: dict[str, Any] | None = None,
    ) -> RunRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, mission_id, status, metadata_json, policy_snapshot_json,
                    policy_snapshot_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    mission_id,
                    RunStatus.CREATED.value,
                    canonical_json(metadata or {}),
                    canonical_json(policy_snapshot) if policy_snapshot else None,
                    str(policy_snapshot["hash"]) if policy_snapshot else None,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id=run_id,
                command_id=None,
                event_type="run.created",
                payload={
                    "mission_id": mission_id,
                    "policy_snapshot_hash": policy_snapshot.get("hash") if policy_snapshot else None,
                },
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
        policy_decision_id: str | None = None,
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
            finished_at = (
                now
                if target
                in {
                    CommandStatus.SUCCEEDED,
                    CommandStatus.FAILED,
                    CommandStatus.REJECTED,
                    CommandStatus.CANCELLED,
                    CommandStatus.INTERRUPTED,
                    CommandStatus.TIMED_OUT,
                }
                else row["finished_at"]
            )
            connection.execute(
                """
                UPDATE commands
                SET status = ?, policy_decision = COALESCE(?, policy_decision),
                    policy_reason = COALESCE(?, policy_reason), approval_id = COALESCE(?, approval_id),
                    policy_decision_id = COALESCE(?, policy_decision_id),
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
                    policy_decision_id,
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

    def create_policy_decision(
        self,
        *,
        command: CommandRecord,
        decision: PolicyDecision,
    ) -> PolicyDecisionRecord:
        decision_id = f"policy-{uuid.uuid4().hex}"
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO policy_decisions(
                    id, run_id, command_id, effect, rule_id, policy_version, reason, risk,
                    required_evidence_json, input_snapshot_json, input_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    command.run_id,
                    command.id,
                    decision.effect.value,
                    decision.rule_id,
                    decision.policy_version,
                    decision.reason,
                    decision.risk.value,
                    canonical_json(list(decision.required_evidence)),
                    canonical_json(decision.input_snapshot),
                    decision.input_hash,
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id=command.run_id,
                command_id=command.id,
                event_type="policy.evaluated",
                payload={
                    "policy_decision_id": decision_id,
                    "effect": decision.effect.value,
                    "rule_id": decision.rule_id,
                    "policy_version": decision.policy_version,
                    "risk": decision.risk.value,
                    "input_hash": decision.input_hash,
                },
                created_at=now,
            )
        return self.require_policy_decision(decision_id)

    def require_policy_decision(self, decision_id: str) -> PolicyDecisionRecord:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM policy_decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(f"policy decision not found: {decision_id}")
        return self._policy_decision_from_row(row)

    def list_policy_decisions(self, command_id: str) -> list[PolicyDecisionRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM policy_decisions WHERE command_id = ? ORDER BY created_at, id",
                (command_id,),
            ).fetchall()
        return [self._policy_decision_from_row(row) for row in rows]

    def create_approval(
        self,
        *,
        command_id: str,
        policy_decision_id: str,
        manifest: dict[str, Any],
        manifest_hash: str,
        expires_at: str,
    ) -> ApprovalRecord:
        command = self.require_command(command_id)
        approval_id = f"approval-{uuid.uuid4().hex}"
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO approvals(
                    id, run_id, command_id, policy_decision_id, manifest_json,
                    manifest_hash, status, requested_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    command.run_id,
                    command.id,
                    policy_decision_id,
                    canonical_json(manifest),
                    manifest_hash,
                    ApprovalStatus.PENDING.value,
                    now,
                    expires_at,
                ),
            )
            self._append_event(
                connection,
                run_id=command.run_id,
                command_id=command.id,
                event_type="approval.requested",
                payload={"approval_id": approval_id, "manifest_hash": manifest_hash, "expires_at": expires_at},
                created_at=now,
            )
        return self.require_approval(approval_id)

    def require_approval(self, approval_id: str) -> ApprovalRecord:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return self._approval_from_row(row)

    def list_approvals(self, command_id: str) -> list[ApprovalRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE command_id = ? ORDER BY requested_at, id", (command_id,)
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        approved_by: str,
        note: str | None,
    ) -> ApprovalRecord:
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ValueError("approval decision must be approved or denied")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            if ApprovalStatus(str(row["status"])) != ApprovalStatus.PENDING:
                raise ValueError("only pending approvals can be decided")
            if str(row["expires_at"]) <= now:
                raise ValueError("approval request has expired")
            connection.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, approved_by = ?, note = ? WHERE id = ?",
                (status.value, now, approved_by, note, approval_id),
            )
            self._append_event(
                connection,
                run_id=str(row["run_id"]),
                command_id=str(row["command_id"]),
                event_type=f"approval.{status.value}",
                payload={"approval_id": approval_id, "approved_by": approved_by, "note": note},
                created_at=now,
            )
        return self.require_approval(approval_id)

    def consume_approval(self, approval_id: str) -> ApprovalRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            if ApprovalStatus(str(row["status"])) != ApprovalStatus.APPROVED:
                raise ValueError("only approved approvals can be consumed")
            connection.execute(
                "UPDATE approvals SET status = ?, consumed_at = ? WHERE id = ?",
                (ApprovalStatus.CONSUMED.value, now, approval_id),
            )
            self._append_event(
                connection,
                run_id=str(row["run_id"]),
                command_id=str(row["command_id"]),
                event_type="approval.consumed",
                payload={"approval_id": approval_id},
                created_at=now,
            )
        return self.require_approval(approval_id)

    def expire_approval(self, approval_id: str) -> ApprovalRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            current = ApprovalStatus(str(row["status"]))
            if current not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                return self._approval_from_row(row)
            connection.execute(
                "UPDATE approvals SET status = ? WHERE id = ?",
                (ApprovalStatus.EXPIRED.value, approval_id),
            )
            self._append_event(
                connection,
                run_id=str(row["run_id"]),
                command_id=str(row["command_id"]),
                event_type="approval.expired",
                payload={"approval_id": approval_id},
                created_at=now,
            )
        return self.require_approval(approval_id)

    def expire_pending_approvals(self, now: str) -> list[ApprovalRecord]:
        with self.database.read() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM approvals WHERE status IN (?, ?) AND expires_at <= ?",
                    (ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value, now),
                ).fetchall()
            ]
        return [self.expire_approval(approval_id) for approval_id in ids]

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
            policy_snapshot=load_json(row["policy_snapshot_json"]),
            policy_snapshot_hash=(
                str(row["policy_snapshot_hash"]) if row["policy_snapshot_hash"] is not None else None
            ),
            error=load_json(row["error_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _policy_decision_from_row(row: sqlite3.Row) -> PolicyDecisionRecord:
        return PolicyDecisionRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            command_id=str(row["command_id"]),
            effect=PolicyEffect(str(row["effect"])),
            rule_id=str(row["rule_id"]),
            policy_version=str(row["policy_version"]),
            reason=str(row["reason"]),
            risk=RiskLevel(str(row["risk"])),
            required_evidence=tuple(load_json(row["required_evidence_json"]) or []),
            input_snapshot=load_json(row["input_snapshot_json"]) or {},
            input_hash=str(row["input_hash"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            command_id=str(row["command_id"]),
            policy_decision_id=str(row["policy_decision_id"]),
            manifest=load_json(row["manifest_json"]) or {},
            manifest_hash=str(row["manifest_hash"]),
            status=ApprovalStatus(str(row["status"])),
            requested_at=str(row["requested_at"]),
            expires_at=str(row["expires_at"]),
            decided_at=str(row["decided_at"]) if row["decided_at"] is not None else None,
            approved_by=str(row["approved_by"]) if row["approved_by"] is not None else None,
            note=str(row["note"]) if row["note"] is not None else None,
            consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
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
            policy_decision_id=(str(row["policy_decision_id"]) if row["policy_decision_id"] is not None else None),
            approval_id=str(row["approval_id"]) if row["approval_id"] is not None else None,
            started_at=str(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
            result=load_json(row["result_json"]),
            error=load_json(row["error_json"]),
            stdout_artifact_id=(str(row["stdout_artifact_id"]) if row["stdout_artifact_id"] is not None else None),
            stderr_artifact_id=(str(row["stderr_artifact_id"]) if row["stderr_artifact_id"] is not None else None),
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
