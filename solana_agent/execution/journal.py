from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from solana_agent.contracts.command import CommandRecord, CommandStatus
from solana_agent.contracts.lifecycle import RunRecord
from solana_agent.storage.repositories import JournalRepository

from .executor import CommandInterrupted, CommandTimedOut, ExecutionRequest, ExecutionResult, Executor
from .idempotency import build_command_idempotency_key


@dataclass(frozen=True, slots=True)
class CommandSpec:
    run_id: str
    step_id: str
    adapter: str
    operation: str
    arguments: dict[str, Any] = field(default_factory=dict)
    cwd: str = "."
    timeout_seconds: int = 60
    expected_state: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("run_id", "step_id", "adapter", "operation", "cwd"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.idempotency_key is not None:
            normalized_key = self.idempotency_key.lower()
            if len(normalized_key) != 64 or any(character not in "0123456789abcdef" for character in normalized_key):
                raise ValueError("idempotency_key must be a 64-character hexadecimal SHA-256 digest")

    def resolved_idempotency_key(self) -> str:
        return self.idempotency_key or build_command_idempotency_key(
            run_id=self.run_id,
            step_id=self.step_id,
            adapter=self.adapter,
            operation=self.operation,
            arguments=self.arguments,
            cwd=self.cwd,
            timeout_seconds=self.timeout_seconds,
            expected_state=self.expected_state,
        )


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    decision: str
    reason: str

    def __post_init__(self) -> None:
        if self.decision not in {"allow", "deny", "require_approval"}:
            raise ValueError(f"unsupported validation decision: {self.decision}")
        if not self.reason.strip():
            raise ValueError("validation reason must not be empty")

    @classmethod
    def allow(cls, reason: str = "command accepted by runtime validation") -> ValidationDecision:
        return cls(decision="allow", reason=reason)

    @classmethod
    def deny(cls, reason: str) -> ValidationDecision:
        return cls(decision="deny", reason=reason)

    @classmethod
    def require_approval(cls, reason: str) -> ValidationDecision:
        return cls(decision="require_approval", reason=reason)


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    command: CommandRecord
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    command: CommandRecord
    duplicate: bool = False


class CommandJournal:
    def __init__(self, repository: JournalRepository) -> None:
        self.repository = repository

    def create_run(
        self,
        *,
        mission_id: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        return self.repository.create_run(
            run_id=run_id or f"run-{uuid.uuid4().hex}",
            mission_id=mission_id,
            metadata=metadata,
        )

    def plan(self, spec: CommandSpec) -> PlanOutcome:
        self.repository.require_run(spec.run_id)
        idempotency_key = spec.resolved_idempotency_key()
        existing = self.repository.find_command_by_idempotency(spec.run_id, idempotency_key)
        if existing is not None:
            return PlanOutcome(command=existing, duplicate=True)

        command_id = f"command-{uuid.uuid4().hex}"
        try:
            command = self.repository.insert_planned_command(
                command_id=command_id,
                run_id=spec.run_id,
                step_id=spec.step_id,
                adapter=spec.adapter,
                operation=spec.operation,
                arguments=spec.arguments,
                cwd=spec.cwd,
                timeout_seconds=spec.timeout_seconds,
                idempotency_key=idempotency_key,
                expected_state=spec.expected_state,
            )
        except sqlite3.IntegrityError:
            concurrent = self.repository.find_command_by_idempotency(spec.run_id, idempotency_key)
            if concurrent is None:
                raise
            return PlanOutcome(command=concurrent, duplicate=True)
        return PlanOutcome(command=command, duplicate=False)

    def validate(self, command_id: str, decision: ValidationDecision) -> CommandRecord:
        command = self.repository.transition_command(command_id, CommandStatus.VALIDATING)
        return self._apply_validation_decision(command, decision)

    def _apply_validation_decision(
        self,
        command: CommandRecord,
        decision: ValidationDecision,
    ) -> CommandRecord:
        if decision.decision == "deny":
            return self.repository.transition_command(
                command.id,
                CommandStatus.REJECTED,
                policy_decision="deny",
                policy_reason=decision.reason,
                error={"code": "validation_rejected", "message": decision.reason},
            )
        if decision.decision == "require_approval":
            return self.repository.transition_command(
                command.id,
                CommandStatus.APPROVAL_REQUIRED,
                policy_decision="require_approval",
                policy_reason=decision.reason,
            )
        return self.repository.transition_command(
            command.id,
            CommandStatus.AUTHORIZED,
            policy_decision="allow",
            policy_reason=decision.reason,
        )

    def authorize_approved(self, command_id: str, *, approval_id: str, reason: str) -> CommandRecord:
        return self.repository.transition_command(
            command_id,
            CommandStatus.AUTHORIZED,
            policy_decision="allow",
            policy_reason=reason,
            approval_id=approval_id,
        )

    def cancel(self, command_id: str, *, reason: str) -> CommandRecord:
        return self.repository.transition_command(
            command_id,
            CommandStatus.CANCELLED,
            error={"code": "command_cancelled", "message": reason},
        )

    def execute(
        self,
        spec: CommandSpec,
        executor: Executor,
        *,
        validation: ValidationDecision | None = None,
        validator: Callable[[CommandRecord], ValidationDecision] | None = None,
    ) -> ExecutionOutcome:
        if validation is not None and validator is not None:
            raise ValueError("provide either validation or validator, not both")
        plan = self.plan(spec)
        if plan.duplicate:
            return ExecutionOutcome(command=plan.command, duplicate=True)

        command = self.repository.transition_command(plan.command.id, CommandStatus.VALIDATING)
        try:
            decision = validator(command) if validator is not None else validation or ValidationDecision.allow()
        except Exception as exc:
            failed = self.repository.transition_command(
                command.id,
                CommandStatus.FAILED,
                error={"code": "validation_exception", "message": str(exc), "type": type(exc).__name__},
            )
            return ExecutionOutcome(command=failed)
        command = self._apply_validation_decision(command, decision)
        if command.status in {CommandStatus.REJECTED, CommandStatus.APPROVAL_REQUIRED}:
            return ExecutionOutcome(command=command)

        command = self.repository.transition_command(command.id, CommandStatus.RUNNING)
        request = ExecutionRequest(
            command_id=command.id,
            adapter=command.adapter,
            operation=command.operation,
            arguments=command.arguments,
            cwd=command.cwd,
            timeout_seconds=command.timeout_seconds,
        )

        try:
            result = executor.execute(request)
        except CommandTimedOut as exc:
            return ExecutionOutcome(command=self._record_timeout(command, exc))
        except CommandInterrupted as exc:
            return ExecutionOutcome(command=self._record_interruption(command, exc))
        except Exception as exc:
            return ExecutionOutcome(command=self._record_exception(command, exc))

        return ExecutionOutcome(command=self._record_result(command, result))

    def recover_orphaned_commands(self, *, reason: str = "runtime restarted without an active process") -> list[CommandRecord]:
        recovered: list[CommandRecord] = []
        for command in self.repository.list_running_commands():
            recovered.append(
                self.repository.transition_command(
                    command.id,
                    CommandStatus.INTERRUPTED,
                    error={"code": "orphaned_command", "message": reason},
                )
            )
        return recovered

    def _record_result(self, command: CommandRecord, result: ExecutionResult) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, result.stdout, result.stderr)
        if result.exit_code == 0:
            return self.repository.transition_command(
                command.id,
                CommandStatus.SUCCEEDED,
                exit_code=result.exit_code,
                result={"metadata": result.metadata},
                stdout_artifact_id=stdout_id,
                stderr_artifact_id=stderr_id,
            )
        return self.repository.transition_command(
            command.id,
            CommandStatus.FAILED,
            exit_code=result.exit_code,
            result={"metadata": result.metadata},
            error={"code": "nonzero_exit", "message": f"executor exited with code {result.exit_code}"},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_timeout(self, command: CommandRecord, exc: CommandTimedOut) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, exc.stdout, exc.stderr)
        return self.repository.transition_command(
            command.id,
            CommandStatus.TIMED_OUT,
            error={"code": "command_timed_out", "message": str(exc)},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_interruption(self, command: CommandRecord, exc: CommandInterrupted) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, exc.stdout, exc.stderr)
        return self.repository.transition_command(
            command.id,
            CommandStatus.INTERRUPTED,
            error={"code": "command_interrupted", "message": str(exc)},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_exception(self, command: CommandRecord, exc: Exception) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, "", "")
        return self.repository.transition_command(
            command.id,
            CommandStatus.FAILED,
            error={"code": "executor_exception", "message": str(exc), "type": type(exc).__name__},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_streams(self, command: CommandRecord, stdout: str, stderr: str) -> tuple[str, str]:
        stdout_artifact = self.repository.create_artifact(
            run_id=command.run_id,
            command_id=command.id,
            kind="stdout",
            content=stdout,
        )
        stderr_artifact = self.repository.create_artifact(
            run_id=command.run_id,
            command_id=command.id,
            kind="stderr",
            content=stderr,
        )
        return stdout_artifact.id, stderr_artifact.id
