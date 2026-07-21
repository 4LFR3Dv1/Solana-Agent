from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solana_agent.authority import ApprovalService
from solana_agent.contracts import (
    AcceptanceDefinition,
    ApprovalStatus,
    MissionDefinition,
    MissionInputDefinition,
    MissionPack,
    MissionStepDefinition,
    MissionStepStatus,
    PreconditionDefinition,
    RuntimeContract,
    hash_payload,
)
from solana_agent.execution import ExecutionRequest, ExecutionResult, FakeExecutor
from solana_agent.missions import MissionEngine
from solana_agent.missions.engine import MissionExecutionError
from solana_agent.storage import Database, JournalRepository


def mission_pack(*steps: MissionStepDefinition, inputs: tuple[MissionInputDefinition, ...] = ()) -> MissionPack:
    mission = MissionDefinition(
        id="test-mission",
        version="1.0.0",
        goal="Exercise the declarative mission engine.",
        inputs=inputs,
        steps=steps,
    )
    payload = {"id": "test-pack", "version": "1.0.0", "missions": {mission.id: mission.to_dict()}}
    return MissionPack("test-pack", "1.0.0", {mission.id: mission}, hash_payload(payload))


def local_contract(tmp_path: Path, **overrides: Any) -> RuntimeContract:
    values: dict[str, Any] = {
        "id": "runtime-test",
        "version": "1.0.0",
        "policy_profile": "local-safe",
        "workspace_root": str(tmp_path),
        "cluster": "localnet",
        "wallet": None,
        "max_lamports": None,
        "tool_versions": {"python": "test"},
    }
    values.update(overrides)
    return RuntimeContract(**values)


def setup_engine(
    tmp_path: Path, executors: dict[str, FakeExecutor]
) -> tuple[MissionEngine, JournalRepository, ApprovalService]:
    database = Database(tmp_path / "state" / "runtime.db")
    database.initialize()
    repository = JournalRepository(database)
    approvals = ApprovalService(repository)
    return MissionEngine(repository, approvals, executors), repository, approvals


def build_test_pack() -> MissionPack:
    return mission_pack(
        MissionStepDefinition("build", "anchor", "build", acceptance=(AcceptanceDefinition("command_succeeded"),)),
        MissionStepDefinition(
            "test",
            "anchor",
            "test",
            depends_on=("build",),
            acceptance=(AcceptanceDefinition("command_succeeded"),),
        ),
    )


def by_id(repository: JournalRepository, run_id: str) -> dict[str, Any]:
    return {step.step_id: step for step in repository.list_mission_steps(run_id)}


def test_engine_executes_dag_and_binds_contract_hashes_to_run(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0, stdout="ok"))
    engine, repository, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = build_test_pack()
    runtime = local_contract(tmp_path)

    outcome = engine.start(pack, "test-mission", inputs={}, runtime_contract=runtime, run_id="run-dag")
    run = repository.require_run(outcome.run_id)

    assert outcome.status == "completed"
    assert executor.call_count == 2
    assert all(step.status == MissionStepStatus.SUCCEEDED for step in outcome.steps)
    assert run.metadata["mission_definition"]["hash"] == pack.missions["test-mission"].definition_hash
    assert run.metadata["mission_pack"]["hash"] == pack.pack_hash
    assert run.metadata["runtime_contract"]["hash"] == runtime.contract_hash


def test_resume_does_not_repeat_succeeded_steps(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0))
    engine, _, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = build_test_pack()
    runtime = local_contract(tmp_path)
    first = engine.start(pack, "test-mission", inputs={}, runtime_contract=runtime, run_id="run-resume")

    resumed = engine.resume(pack, first.run_id, inputs={}, runtime_contract=runtime)

    assert resumed.status == "completed"
    assert resumed.skipped_steps == ("build", "test")
    assert executor.call_count == 2


def test_failed_dependency_blocks_downstream_step(tmp_path: Path) -> None:
    def callback(request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(exit_code=2 if request.operation == "build" else 0)

    executor = FakeExecutor(callback=callback)
    engine, repository, _ = setup_engine(tmp_path, {"anchor": executor})

    outcome = engine.start(
        build_test_pack(), "test-mission", inputs={}, runtime_contract=local_contract(tmp_path), run_id="run-fail"
    )
    steps = by_id(repository, outcome.run_id)

    assert outcome.status == "failed"
    assert steps["build"].status == MissionStepStatus.FAILED
    assert steps["test"].status == MissionStepStatus.BLOCKED
    assert executor.call_count == 1


def test_resume_retries_failed_step_then_unblocks_dependents(tmp_path: Path) -> None:
    build_attempts = 0

    def callback(request: ExecutionRequest) -> ExecutionResult:
        nonlocal build_attempts
        if request.operation == "build":
            build_attempts += 1
            return ExecutionResult(exit_code=1 if build_attempts == 1 else 0)
        return ExecutionResult(exit_code=0)

    executor = FakeExecutor(callback=callback)
    engine, repository, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = build_test_pack()
    runtime = local_contract(tmp_path)
    first = engine.start(pack, "test-mission", inputs={}, runtime_contract=runtime, run_id="run-retry")

    resumed = engine.resume(pack, first.run_id, inputs={}, runtime_contract=runtime)
    steps = by_id(repository, resumed.run_id)

    assert resumed.status == "completed"
    assert steps["build"].attempt == 2
    assert steps["test"].attempt == 1
    assert executor.call_count == 3


def test_acceptance_failure_fails_step_even_when_command_succeeds(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0, metadata={"verified": False}))
    engine, repository, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = mission_pack(
        MissionStepDefinition(
            "verify",
            "anchor",
            "build",
            acceptance=(
                AcceptanceDefinition(
                    "result_equals",
                    {"path": "metadata.verified", "expected": True},
                ),
            ),
        )
    )

    outcome = engine.start(pack, "test-mission", inputs={}, runtime_contract=local_contract(tmp_path))
    step = repository.require_mission_step(outcome.run_id, "verify")

    assert outcome.status == "failed"
    assert step.error and step.error["code"] == "acceptance_failed"


def test_failed_precondition_is_recorded_without_executor_call(tmp_path: Path) -> None:
    executor = FakeExecutor()
    engine, repository, _ = setup_engine(tmp_path, {"filesystem": executor})
    missing = tmp_path / "missing"
    pack = mission_pack(
        MissionStepDefinition(
            "inspect",
            "filesystem",
            "read",
            arguments={"path": str(missing)},
            preconditions=(PreconditionDefinition("path_exists", {"path": str(missing)}),),
        )
    )

    outcome = engine.start(pack, "test-mission", inputs={}, runtime_contract=local_contract(tmp_path))
    step = repository.require_mission_step(outcome.run_id, "inspect")

    assert outcome.status == "failed"
    assert step.error and step.error["code"] == "precondition_failed"
    assert executor.call_count == 0


def test_approval_pauses_and_resume_consumes_before_execution(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0, stdout="deployed"))
    engine, repository, approvals = setup_engine(tmp_path, {"solana": executor})
    pack = mission_pack(MissionStepDefinition("deploy", "solana", "deploy"))
    runtime = local_contract(
        tmp_path,
        policy_profile="devnet-safe",
        cluster="devnet",
        wallet="11111111111111111111111111111111",
        max_lamports=1_000_000,
    )

    waiting = engine.start(pack, "test-mission", inputs={}, runtime_contract=runtime, run_id="run-approval")
    step = repository.require_mission_step(waiting.run_id, "deploy")
    command = repository.require_command(step.command_id or "")
    approval = approvals.approve(command.approval_id or "", approved_by="operator")

    resumed = engine.resume(pack, waiting.run_id, inputs={}, runtime_contract=runtime)

    assert waiting.status == "waiting_approval"
    assert resumed.status == "completed"
    assert repository.require_approval(approval.id).status == ApprovalStatus.CONSUMED
    assert executor.call_count == 1


def test_resume_rejects_changed_inputs(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0))
    engine, _, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = mission_pack(
        MissionStepDefinition("build", "anchor", "build", arguments={"name": "{{inputs.name}}"}),
        inputs=(MissionInputDefinition("name"),),
    )
    runtime = local_contract(tmp_path)
    first = engine.start(pack, "test-mission", inputs={"name": "first"}, runtime_contract=runtime)

    with pytest.raises(MissionExecutionError, match="resume contract mismatch"):
        engine.resume(pack, first.run_id, inputs={"name": "changed"}, runtime_contract=runtime)


def test_resume_rejects_changed_runtime_contract(tmp_path: Path) -> None:
    executor = FakeExecutor(ExecutionResult(exit_code=0))
    engine, _, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = build_test_pack()
    runtime = local_contract(tmp_path)
    first = engine.start(pack, "test-mission", inputs={}, runtime_contract=runtime)
    changed = local_contract(tmp_path, tool_versions={"python": "changed"})

    with pytest.raises(MissionExecutionError, match="resume contract mismatch"):
        engine.resume(pack, first.run_id, inputs={}, runtime_contract=changed)


def test_secret_input_is_redacted_before_run_persistence(tmp_path: Path) -> None:
    secret = "private key: never-persist-this"
    executor = FakeExecutor()
    engine, repository, _ = setup_engine(tmp_path, {"anchor": executor})
    pack = mission_pack(
        MissionStepDefinition("build", "anchor", "build", arguments={"credential": "{{inputs.credential}}"}),
        inputs=(MissionInputDefinition("credential"),),
    )

    outcome = engine.start(
        pack,
        "test-mission",
        inputs={"credential": secret},
        runtime_contract=local_contract(tmp_path),
    )
    run = repository.require_run(outcome.run_id)

    assert run.metadata["inputs"]["credential"] == "[REDACTED]"
    assert secret.encode() not in repository.database.path.read_bytes()
    assert executor.call_count == 0


def test_database_applies_mission_step_migration(tmp_path: Path) -> None:
    database = Database(tmp_path / "migration.db")
    database.initialize()

    with database.read() as connection:
        versions = [int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        tables = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    assert versions == [1, 2, 3]
    assert "mission_steps" in tables
