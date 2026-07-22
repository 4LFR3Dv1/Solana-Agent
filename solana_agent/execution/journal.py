from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from solana_agent.authority.approvals import ApprovalError, ApprovalService
from solana_agent.authority.policy import PolicyEngine
from solana_agent.authority.redaction import redact_mapping, redact_text
from solana_agent.contracts.authority import PolicyContext, PolicyDecision, PolicyEffect
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
    def __init__(
        self,
        repository: JournalRepository,
        *,
        policy_engine: PolicyEngine | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.repository = repository
        self.policy_engine = policy_engine
        self.approval_service = approval_service

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
            policy_snapshot=self.policy_engine.snapshot() if self.policy_engine else None,
        )

    def plan(self, spec: CommandSpec) -> PlanOutcome:
        self.repository.require_run(spec.run_id)
        redaction = redact_mapping(spec.arguments)
        safe_spec = replace(spec, arguments=redaction.value)
        idempotency_key = safe_spec.resolved_idempotency_key()
        existing = self.repository.find_command_by_idempotency(spec.run_id, idempotency_key)
        if existing is not None:
            return PlanOutcome(command=existing, duplicate=True)

        command_id = f"command-{uuid.uuid4().hex}"
        try:
            command = self.repository.insert_planned_command(
                command_id=command_id,
                run_id=safe_spec.run_id,
                step_id=safe_spec.step_id,
                adapter=safe_spec.adapter,
                operation=safe_spec.operation,
                arguments=safe_spec.arguments,
                cwd=safe_spec.cwd,
                timeout_seconds=safe_spec.timeout_seconds,
                idempotency_key=idempotency_key,
                expected_state=safe_spec.expected_state,
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
        safe_reason = redact_text(decision.reason)
        if decision.decision == "deny":
            return self.repository.transition_command(
                command.id,
                CommandStatus.REJECTED,
                policy_decision="deny",
                policy_reason=safe_reason,
                error={"code": "validation_rejected", "message": safe_reason},
            )
        if decision.decision == "require_approval":
            return self.repository.transition_command(
                command.id,
                CommandStatus.APPROVAL_REQUIRED,
                policy_decision="require_approval",
                policy_reason=safe_reason,
            )
        return self.repository.transition_command(
            command.id,
            CommandStatus.AUTHORIZED,
            policy_decision="allow",
            policy_reason=safe_reason,
        )

    def cancel(self, command_id: str, *, reason: str) -> CommandRecord:
        safe_reason = redact_text(reason)
        return self.repository.transition_command(
            command_id,
            CommandStatus.CANCELLED,
            error={"code": "command_cancelled", "message": safe_reason},
        )

    def execute(
        self,
        spec: CommandSpec,
        executor: Executor,
        *,
        validation: ValidationDecision | None = None,
        validator: Callable[[CommandRecord], ValidationDecision] | None = None,
        policy_context: PolicyContext | None = None,
    ) -> ExecutionOutcome:
        if validation is not None and validator is not None:
            raise ValueError("provide either validation or validator, not both")
        plan = self.plan(spec)
        if plan.duplicate:
            return ExecutionOutcome(command=plan.command, duplicate=True)

        command = self.repository.transition_command(plan.command.id, CommandStatus.VALIDATING)
        try:
            if validator is not None:
                decision = validator(command)
            elif validation is not None:
                decision = validation
            elif self.policy_engine is not None:
                if policy_context is None:
                    decision = ValidationDecision.deny("policy context is required for governed execution")
                else:
                    governed = self.policy_engine.evaluate(command, policy_context)
                    command = self._apply_policy_decision(command, governed)
                    if command.status in {CommandStatus.REJECTED, CommandStatus.APPROVAL_REQUIRED}:
                        return ExecutionOutcome(command=command)
                    return self._execute_authorized(command, executor)
            else:
                decision = ValidationDecision.deny("no policy decision was provided; default deny")
        except Exception as exc:
            failed = self.repository.transition_command(
                command.id,
                CommandStatus.FAILED,
                error={
                    "code": "validation_exception",
                    "message": redact_text(str(exc)),
                    "type": type(exc).__name__,
                },
            )
            return ExecutionOutcome(command=failed)
        command = self._apply_validation_decision(command, decision)
        if command.status in {CommandStatus.REJECTED, CommandStatus.APPROVAL_REQUIRED}:
            return ExecutionOutcome(command=command)

        return self._execute_authorized(command, executor)

    def execute_approved(self, command_id: str, executor: Executor) -> ExecutionOutcome:
        command = self.repository.require_command(command_id)
        if command.status != CommandStatus.APPROVAL_REQUIRED:
            raise ValueError("command is not awaiting approval")
        if self.approval_service is None or command.approval_id is None:
            return ExecutionOutcome(
                command=self.repository.transition_command(
                    command.id,
                    CommandStatus.REJECTED,
                    error={"code": "approval_unavailable", "message": "approval service or request is missing"},
                )
            )
        try:
            approval = self.approval_service.consume(command.approval_id, command)
        except ApprovalError as exc:
            return ExecutionOutcome(
                command=self.repository.transition_command(
                    command.id,
                    CommandStatus.REJECTED,
                    error={"code": "approval_invalid", "message": str(exc)},
                )
            )
        command = self.repository.transition_command(
            command.id,
            CommandStatus.AUTHORIZED,
            policy_decision="allow",
            policy_reason="bound operator approval validated and consumed",
            approval_id=approval.id,
            payload={"approval_id": approval.id, "manifest_hash": approval.manifest_hash},
        )
        return self._execute_authorized(command, executor)

    def _apply_policy_decision(self, command: CommandRecord, decision: PolicyDecision) -> CommandRecord:
        record = self.repository.create_policy_decision(command=command, decision=decision)
        payload = {
            "policy_decision_id": record.id,
            "rule_id": record.rule_id,
            "policy_version": record.policy_version,
            "risk": record.risk.value,
            "input_hash": record.input_hash,
            "required_evidence": list(record.required_evidence),
        }
        if decision.effect == PolicyEffect.DENY:
            return self.repository.transition_command(
                command.id,
                CommandStatus.REJECTED,
                policy_decision="deny",
                policy_reason=decision.reason,
                policy_decision_id=record.id,
                payload=payload,
                error={"code": "policy_rejected", "message": decision.reason},
            )
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            if self.approval_service is None:
                return self.repository.transition_command(
                    command.id,
                    CommandStatus.REJECTED,
                    policy_decision="deny",
                    policy_reason="policy requires approval but no approval service is configured",
                    policy_decision_id=record.id,
                    payload=payload,
                    error={"code": "approval_service_missing", "message": "approval service is not configured"},
                )
            approval = self.approval_service.request(command, record)
            return self.repository.transition_command(
                command.id,
                CommandStatus.APPROVAL_REQUIRED,
                policy_decision="require_approval",
                policy_reason=decision.reason,
                policy_decision_id=record.id,
                approval_id=approval.id,
                payload={**payload, "approval_id": approval.id, "approval_manifest_hash": approval.manifest_hash},
            )
        return self.repository.transition_command(
            command.id,
            CommandStatus.AUTHORIZED,
            policy_decision="allow",
            policy_reason=decision.reason,
            policy_decision_id=record.id,
            payload=payload,
        )

    def _execute_authorized(self, command: CommandRecord, executor: Executor) -> ExecutionOutcome:
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

    def recover_orphaned_commands(
        self, *, reason: str = "runtime restarted without an active process"
    ) -> list[CommandRecord]:
        if self.approval_service is not None:
            self.approval_service.expire_pending()
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
        safe_metadata = redact_mapping(result.metadata).value
        if result.exit_code == 0:
            return self.repository.transition_command(
                command.id,
                CommandStatus.SUCCEEDED,
                exit_code=result.exit_code,
                result={"metadata": safe_metadata},
                stdout_artifact_id=stdout_id,
                stderr_artifact_id=stderr_id,
            )
        return self.repository.transition_command(
            command.id,
            CommandStatus.FAILED,
            exit_code=result.exit_code,
            result={"metadata": safe_metadata},
            error={"code": "nonzero_exit", "message": f"executor exited with code {result.exit_code}"},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_timeout(self, command: CommandRecord, exc: CommandTimedOut) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, exc.stdout, exc.stderr)
        return self.repository.transition_command(
            command.id,
            CommandStatus.TIMED_OUT,
            error={"code": "command_timed_out", "message": redact_text(str(exc))},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_interruption(self, command: CommandRecord, exc: CommandInterrupted) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, exc.stdout, exc.stderr)
        return self.repository.transition_command(
            command.id,
            CommandStatus.INTERRUPTED,
            error={"code": "command_interrupted", "message": redact_text(str(exc))},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_exception(self, command: CommandRecord, exc: Exception) -> CommandRecord:
        stdout_id, stderr_id = self._record_streams(command, "", "")
        return self.repository.transition_command(
            command.id,
            CommandStatus.FAILED,
            error={"code": "executor_exception", "message": redact_text(str(exc)), "type": type(exc).__name__},
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
        )

    def _record_streams(self, command: CommandRecord, stdout: str, stderr: str) -> tuple[str, str]:
        stdout_artifact = self.repository.create_artifact(
            run_id=command.run_id,
            command_id=command.id,
            kind="stdout",
            content=redact_text(stdout),
        )
        stderr_artifact = self.repository.create_artifact(
            run_id=command.run_id,
            command_id=command.id,
            kind="stderr",
            content=redact_text(stderr),
        )
        return stdout_artifact.id, stderr_artifact.id
