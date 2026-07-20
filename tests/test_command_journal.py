from __future__ import annotations

import hashlib
from pathlib import Path

from solana_agent.contracts.command import CommandRecord, CommandStatus
from solana_agent.execution import (
    CommandInterrupted,
    CommandJournal,
    CommandSpec,
    CommandTimedOut,
    ExecutionRequest,
    ExecutionResult,
    FakeExecutor,
    ValidationDecision,
)
from solana_agent.storage import Database, JournalRepository


def make_journal(tmp_path: Path) -> tuple[CommandJournal, JournalRepository, Path]:
    database_path = tmp_path / "state" / "runtime.db"
    database = Database(database_path)
    database.initialize()
    repository = JournalRepository(database)
    journal = CommandJournal(repository)
    journal.create_run(run_id="run-1", mission_id="mission-test")
    return journal, repository, database_path


def command_spec(**overrides: object) -> CommandSpec:
    values = {
        "run_id": "run-1",
        "step_id": "build",
        "adapter": "fake",
        "operation": "build",
        "arguments": {"release": False},
        "cwd": "/workspace",
        "timeout_seconds": 60,
    }
    values.update(overrides)
    return CommandSpec(**values)  # type: ignore[arg-type]


def test_journal_persists_planned_before_executor_is_called(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)

    def assert_running(request: ExecutionRequest) -> ExecutionResult:
        command_id = str(request.command_id)
        command = repository.require_command(command_id)
        events = repository.list_events(command.run_id)
        assert command.status == CommandStatus.RUNNING
        assert [event.event_type for event in events[:4]] == [
            "run.created",
            "command.planned",
            "command.validating",
            "command.authorized",
        ]
        return ExecutionResult(exit_code=0, stdout="built", stderr="")

    outcome = journal.execute(
        command_spec(), FakeExecutor(callback=assert_running), validation=ValidationDecision.allow()
    )

    assert outcome.command.status == CommandStatus.SUCCEEDED


def test_rejected_command_is_persisted_without_calling_executor(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)
    executor = FakeExecutor()

    outcome = journal.execute(
        command_spec(),
        executor,
        validation=ValidationDecision.deny("adapter is not allowlisted"),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert outcome.command.error == {
        "code": "validation_rejected",
        "message": "adapter is not allowlisted",
    }
    assert executor.call_count == 0
    assert repository.require_command(outcome.command.id).status == CommandStatus.REJECTED


def test_validator_runs_after_intent_and_validating_state_are_persisted(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)
    executor = FakeExecutor()

    def validator(command: CommandRecord) -> ValidationDecision:
        persisted = repository.require_command(command.id)
        events = repository.list_events(persisted.run_id)
        assert persisted.status == CommandStatus.VALIDATING
        assert [event.event_type for event in events[-2:]] == ["command.planned", "command.validating"]
        return ValidationDecision.deny("blocked by test policy")

    outcome = journal.execute(command_spec(), executor, validator=validator)

    assert outcome.command.status == CommandStatus.REJECTED
    assert executor.call_count == 0


def test_validator_exception_is_persisted_without_execution(tmp_path: Path) -> None:
    journal, _, _ = make_journal(tmp_path)
    executor = FakeExecutor()

    def broken_validator(command: CommandRecord) -> ValidationDecision:
        del command
        raise RuntimeError("policy backend unavailable")

    outcome = journal.execute(command_spec(), executor, validator=broken_validator)

    assert outcome.command.status == CommandStatus.FAILED
    assert outcome.command.error == {
        "code": "validation_exception",
        "message": "policy backend unavailable",
        "type": "RuntimeError",
    }
    assert executor.call_count == 0


def test_stdout_and_stderr_are_separate_hashed_artifacts(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)
    executor = FakeExecutor(ExecutionResult(exit_code=0, stdout="standard output", stderr="warning output"))

    outcome = journal.execute(command_spec(), executor, validation=ValidationDecision.allow())
    artifacts = repository.list_artifacts(outcome.command.id)
    by_kind = {artifact.kind: artifact for artifact in artifacts}

    assert outcome.command.status == CommandStatus.SUCCEEDED
    assert by_kind["stdout"].content == "standard output"
    assert by_kind["stderr"].content == "warning output"
    assert by_kind["stdout"].content_hash == hashlib.sha256(b"standard output").hexdigest()
    assert outcome.command.stdout_artifact_id == by_kind["stdout"].id
    assert outcome.command.stderr_artifact_id == by_kind["stderr"].id


def test_nonzero_exit_is_structured_failure(tmp_path: Path) -> None:
    journal, _, _ = make_journal(tmp_path)
    executor = FakeExecutor(ExecutionResult(exit_code=7, stdout="partial", stderr="compile failed"))

    outcome = journal.execute(command_spec(), executor, validation=ValidationDecision.allow())

    assert outcome.command.status == CommandStatus.FAILED
    assert outcome.command.exit_code == 7
    assert outcome.command.error == {"code": "nonzero_exit", "message": "executor exited with code 7"}


def test_executor_exception_is_persisted_as_terminal_failure(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)

    outcome = journal.execute(
        command_spec(),
        FakeExecutor(error=RuntimeError("adapter crashed")),
        validation=ValidationDecision.allow(),
    )

    assert outcome.command.status == CommandStatus.FAILED
    assert outcome.command.error == {
        "code": "executor_exception",
        "message": "adapter crashed",
        "type": "RuntimeError",
    }
    assert repository.require_command(outcome.command.id).finished_at is not None


def test_timeout_preserves_partial_streams(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)
    timeout = CommandTimedOut("build exceeded 60s", stdout="partial stdout", stderr="partial stderr")

    outcome = journal.execute(command_spec(), FakeExecutor(error=timeout), validation=ValidationDecision.allow())
    artifacts = {item.kind: item for item in repository.list_artifacts(outcome.command.id)}

    assert outcome.command.status == CommandStatus.TIMED_OUT
    assert outcome.command.error == {"code": "command_timed_out", "message": "build exceeded 60s"}
    assert artifacts["stdout"].content == "partial stdout"
    assert artifacts["stderr"].content == "partial stderr"


def test_interruption_is_distinct_from_failure(tmp_path: Path) -> None:
    journal, _, _ = make_journal(tmp_path)
    interruption = CommandInterrupted("stopped by operator", stdout="before stop")

    outcome = journal.execute(command_spec(), FakeExecutor(error=interruption), validation=ValidationDecision.allow())

    assert outcome.command.status == CommandStatus.INTERRUPTED
    assert outcome.command.error == {"code": "command_interrupted", "message": "stopped by operator"}


def test_duplicate_command_does_not_execute_twice(tmp_path: Path) -> None:
    journal, _, _ = make_journal(tmp_path)
    executor = FakeExecutor(ExecutionResult(exit_code=0, stdout="ok"))
    spec = command_spec()

    first = journal.execute(spec, executor, validation=ValidationDecision.allow())
    second = journal.execute(spec, executor, validation=ValidationDecision.allow())

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.command.id == first.command.id
    assert executor.call_count == 1


def test_approval_required_stops_before_execution(tmp_path: Path) -> None:
    journal, _, _ = make_journal(tmp_path)
    executor = FakeExecutor()

    outcome = journal.execute(
        command_spec(operation="deploy"),
        executor,
        validation=ValidationDecision.require_approval("deploy needs operator approval"),
    )

    assert outcome.command.status == CommandStatus.APPROVAL_REQUIRED
    assert executor.call_count == 0


def test_event_sequence_is_append_only_and_monotonic(tmp_path: Path) -> None:
    journal, repository, _ = make_journal(tmp_path)

    journal.execute(command_spec(), FakeExecutor(ExecutionResult(exit_code=0)), validation=ValidationDecision.allow())
    events = repository.list_events("run-1")

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].event_type == "command.succeeded"


def test_database_can_be_reopened_without_losing_history(tmp_path: Path) -> None:
    journal, _, database_path = make_journal(tmp_path)
    outcome = journal.execute(
        command_spec(),
        FakeExecutor(ExecutionResult(exit_code=0, stdout="persisted")),
        validation=ValidationDecision.allow(),
    )

    reopened_database = Database(database_path)
    reopened_database.initialize()
    reopened_repository = JournalRepository(reopened_database)

    assert reopened_repository.require_command(outcome.command.id).status == CommandStatus.SUCCEEDED
    assert reopened_repository.list_artifacts(outcome.command.id)[0].content in {"persisted", ""}


def test_recovery_marks_running_command_as_interrupted(tmp_path: Path) -> None:
    journal, repository, database_path = make_journal(tmp_path)
    planned = journal.plan(command_spec()).command
    authorized = journal.validate(planned.id, ValidationDecision.allow())
    repository.transition_command(authorized.id, CommandStatus.RUNNING)

    restarted_database = Database(database_path)
    restarted_database.initialize()
    restarted_journal = CommandJournal(JournalRepository(restarted_database))
    recovered = restarted_journal.recover_orphaned_commands()

    assert len(recovered) == 1
    assert recovered[0].status == CommandStatus.INTERRUPTED
    assert recovered[0].error == {
        "code": "orphaned_command",
        "message": "runtime restarted without an active process",
    }
