from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CommandStatus(StrEnum):
    PLANNED = "planned"
    VALIDATING = "validating"
    APPROVAL_REQUIRED = "approval_required"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


TERMINAL_COMMAND_STATUSES = frozenset(
    {
        CommandStatus.SUCCEEDED,
        CommandStatus.FAILED,
        CommandStatus.REJECTED,
        CommandStatus.CANCELLED,
        CommandStatus.INTERRUPTED,
        CommandStatus.TIMED_OUT,
    }
)


COMMAND_STATUS_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.PLANNED: frozenset({CommandStatus.VALIDATING, CommandStatus.CANCELLED}),
    CommandStatus.VALIDATING: frozenset(
        {
            CommandStatus.APPROVAL_REQUIRED,
            CommandStatus.AUTHORIZED,
            CommandStatus.REJECTED,
            CommandStatus.FAILED,
            CommandStatus.CANCELLED,
        }
    ),
    CommandStatus.APPROVAL_REQUIRED: frozenset(
        {CommandStatus.AUTHORIZED, CommandStatus.REJECTED, CommandStatus.CANCELLED}
    ),
    CommandStatus.AUTHORIZED: frozenset(
        {CommandStatus.RUNNING, CommandStatus.FAILED, CommandStatus.CANCELLED}
    ),
    CommandStatus.RUNNING: frozenset(
        {
            CommandStatus.SUCCEEDED,
            CommandStatus.FAILED,
            CommandStatus.INTERRUPTED,
            CommandStatus.TIMED_OUT,
        }
    ),
    CommandStatus.SUCCEEDED: frozenset(),
    CommandStatus.FAILED: frozenset(),
    CommandStatus.REJECTED: frozenset(),
    CommandStatus.CANCELLED: frozenset(),
    CommandStatus.INTERRUPTED: frozenset(),
    CommandStatus.TIMED_OUT: frozenset(),
}


class InvalidCommandTransition(ValueError):
    def __init__(self, current: CommandStatus, target: CommandStatus) -> None:
        super().__init__(f"invalid command transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def is_terminal_command_status(status: CommandStatus) -> bool:
    return status in TERMINAL_COMMAND_STATUSES


def require_command_transition(current: CommandStatus, target: CommandStatus) -> None:
    if target not in COMMAND_STATUS_TRANSITIONS[current]:
        raise InvalidCommandTransition(current, target)


@dataclass(frozen=True, slots=True)
class CommandRecord:
    id: str
    run_id: str
    step_id: str
    adapter: str
    operation: str
    arguments: dict[str, Any]
    cwd: str
    timeout_seconds: int
    idempotency_key: str
    status: CommandStatus
    expected_state: str | None
    policy_decision: str | None
    policy_reason: str | None
    approval_id: str | None
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    stdout_artifact_id: str | None
    stderr_artifact_id: str | None
    created_at: str
    updated_at: str
