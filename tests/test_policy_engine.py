from __future__ import annotations

import json
from pathlib import Path

import pytest

from solana_agent.authority import ApprovalService, PolicyEngine, PolicyProfile
from solana_agent.contracts import ApprovalStatus, CommandStatus, PolicyContext, PolicyEffect
from solana_agent.execution import CommandJournal, CommandSpec, ExecutionResult, FakeExecutor
from solana_agent.storage import Database, JournalRepository


def governed_runtime(
    tmp_path: Path,
    *,
    profile: PolicyProfile = PolicyProfile.DEVNET_SAFE,
) -> tuple[CommandJournal, JournalRepository, ApprovalService, Path]:
    database = Database(tmp_path / "state" / "runtime.db")
    database.initialize()
    repository = JournalRepository(database)
    approvals = ApprovalService(repository)
    engine = PolicyEngine(profile)
    journal = CommandJournal(repository, policy_engine=engine, approval_service=approvals)
    journal.create_run(run_id="run-policy", mission_id="mission-policy")
    return journal, repository, approvals, database.path


def spec(tmp_path: Path, *, adapter: str = "solana", operation: str = "deploy", **arguments: object) -> CommandSpec:
    return CommandSpec(
        run_id="run-policy",
        step_id=operation,
        adapter=adapter,
        operation=operation,
        arguments=dict(arguments),
        cwd=str(tmp_path),
    )


def context(tmp_path: Path, **overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "workspace_root": str(tmp_path),
        "cluster": "devnet",
        "wallet": "11111111111111111111111111111111",
        "max_lamports": 1_000_000,
        "runtime_contract_hash": "a" * 64,
    }
    values.update(overrides)
    return PolicyContext(**values)  # type: ignore[arg-type]


def test_journal_is_default_deny_without_explicit_policy(tmp_path: Path) -> None:
    database = Database(tmp_path / "runtime.db")
    database.initialize()
    repository = JournalRepository(database)
    journal = CommandJournal(repository)
    journal.create_run(run_id="run-policy", mission_id="default-deny")
    executor = FakeExecutor()

    outcome = journal.execute(spec(tmp_path, adapter="anchor", operation="build"), executor)

    assert outcome.command.status == CommandStatus.REJECTED
    assert outcome.command.policy_reason == "no policy decision was provided; default deny"
    assert executor.call_count == 0


def test_unknown_operation_is_denied_and_decision_is_audited(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)
    executor = FakeExecutor()

    outcome = journal.execute(
        spec(tmp_path, adapter="shell", operation="arbitrary"), executor, policy_context=context(tmp_path)
    )
    decisions = repository.list_policy_decisions(outcome.command.id)

    assert outcome.command.status == CommandStatus.REJECTED
    assert executor.call_count == 0
    assert len(decisions) == 1
    assert decisions[0].effect == PolicyEffect.DENY
    assert decisions[0].rule_id == "default-deny"
    assert decisions[0].input_hash


def test_local_build_is_allowed_by_local_safe_profile(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.LOCAL_SAFE)

    outcome = journal.execute(
        spec(tmp_path, adapter="anchor", operation="build"),
        FakeExecutor(ExecutionResult(exit_code=0, stdout="built")),
        policy_context=context(tmp_path, cluster="localnet", max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.SUCCEEDED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "anchor-build"


def test_anchor_deploy_requires_a_bound_approval(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)

    outcome = journal.execute(
        spec(tmp_path, adapter="anchor", operation="deploy"),
        FakeExecutor(),
        policy_context=context(tmp_path),
    )

    assert outcome.command.status == CommandStatus.APPROVAL_REQUIRED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "anchor-devnet-deploy"


def test_allowlisted_rpc_read_is_available_in_read_only_profile(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.READ_ONLY)

    outcome = journal.execute(
        spec(tmp_path, adapter="solana_rpc", operation="get_health"),
        FakeExecutor(ExecutionResult(exit_code=0, stdout='{"result":"ok"}')),
        policy_context=context(tmp_path, max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.SUCCEEDED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "rpc-read"


def test_wallet_balance_requirement_is_an_allowlisted_read(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.READ_ONLY)

    outcome = journal.execute(
        spec(tmp_path, adapter="solana", operation="require_balance", minimum_lamports=2_000_000_000),
        FakeExecutor(ExecutionResult(exit_code=0, stdout="5000000000 lamports")),
        policy_context=context(tmp_path, max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.SUCCEEDED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "require-wallet-balance"


def test_read_only_profile_blocks_local_build(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.READ_ONLY)

    outcome = journal.execute(
        spec(tmp_path, adapter="anchor", operation="build"),
        FakeExecutor(),
        policy_context=context(tmp_path, cluster="localnet", max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "default-deny"


@pytest.mark.parametrize("cluster", ["mainnet", "mainnet-beta"])
def test_mainnet_is_always_blocked(tmp_path: Path, cluster: str) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)

    outcome = journal.execute(spec(tmp_path), FakeExecutor(), policy_context=context(tmp_path, cluster=cluster))

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "cluster-mainnet"


def test_unknown_rpc_endpoint_is_blocked(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)

    outcome = journal.execute(
        spec(tmp_path),
        FakeExecutor(),
        policy_context=context(tmp_path, cluster="https://untrusted-rpc.example"),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "cluster-not-allowlisted"


def test_path_escape_is_blocked(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.LOCAL_SAFE)
    outside = tmp_path.parent / "outside"

    outcome = journal.execute(
        spec(tmp_path, adapter="filesystem", operation="read", path=str(outside)),
        FakeExecutor(),
        policy_context=context(tmp_path, cluster=None, max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "path-escape"


def test_program_binary_path_escape_is_blocked_before_deploy(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)
    outside_program = tmp_path.parent / "untrusted.so"

    outcome = journal.execute(
        spec(tmp_path, adapter="solana", operation="deploy", program_path=str(outside_program)),
        FakeExecutor(),
        policy_context=context(tmp_path),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "path-escape"


def test_existing_workspace_requires_approval(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.LOCAL_SAFE)
    destination = tmp_path / "existing"
    destination.mkdir()

    outcome = journal.execute(
        spec(tmp_path, adapter="filesystem", operation="create_workspace", destination=str(destination)),
        FakeExecutor(),
        policy_context=context(tmp_path, cluster=None, max_lamports=None),
    )

    assert outcome.command.status == CommandStatus.APPROVAL_REQUIRED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "workspace-exists"


def test_invalid_wallet_is_denied_and_redacted_from_policy_snapshot(tmp_path: Path) -> None:
    journal, repository, _, database_path = governed_runtime(tmp_path)
    secret_like_wallet = "this is not a public key and must not persist"

    outcome = journal.execute(
        spec(tmp_path),
        FakeExecutor(),
        policy_context=context(tmp_path, wallet=secret_like_wallet),
    )
    decision = repository.list_policy_decisions(outcome.command.id)[0]

    assert decision.rule_id == "wallet-invalid"
    assert decision.input_snapshot["context"]["wallet"] == "[REDACTED]"
    assert secret_like_wallet.encode() not in database_path.read_bytes()


def test_spend_above_profile_limit_is_denied(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)

    outcome = journal.execute(
        spec(tmp_path),
        FakeExecutor(),
        policy_context=context(tmp_path, max_lamports=2_000_000_001),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "spend-limit"


def test_material_operation_requires_runtime_contract_hash(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)

    outcome = journal.execute(
        spec(tmp_path),
        FakeExecutor(),
        policy_context=context(tmp_path, runtime_contract_hash=None),
    )

    assert outcome.command.status == CommandStatus.REJECTED
    assert repository.list_policy_decisions(outcome.command.id)[0].rule_id == "runtime-contract-invalid"


def test_secret_is_redacted_before_any_persistence_and_execution_is_denied(tmp_path: Path) -> None:
    journal, repository, _, database_path = governed_runtime(tmp_path)
    secret = "private key: this-value-must-never-reach-the-ledger"

    outcome = journal.execute(
        spec(tmp_path, adapter="anchor", operation="build", private_key=secret),
        FakeExecutor(),
        policy_context=context(tmp_path, cluster="localnet", max_lamports=None),
    )

    persisted = repository.require_command(outcome.command.id)
    assert persisted.status == CommandStatus.REJECTED
    assert persisted.arguments["private_key"] == "[REDACTED]"
    assert repository.list_policy_decisions(persisted.id)[0].rule_id == "secret-input"
    assert secret.encode() not in database_path.read_bytes()


def test_executor_outputs_and_metadata_are_redacted_before_persistence(tmp_path: Path) -> None:
    journal, repository, _, database_path = governed_runtime(tmp_path, profile=PolicyProfile.LOCAL_SAFE)
    secret = "do-not-persist-this-secret"

    outcome = journal.execute(
        spec(tmp_path, adapter="anchor", operation="build"),
        FakeExecutor(
            ExecutionResult(
                exit_code=0,
                stdout=f"private_key={secret}",
                stderr=f'{{"seed_phrase":"{secret}"}}',
                metadata={"password": secret},
            )
        ),
        policy_context=context(tmp_path, cluster="localnet", max_lamports=None),
    )
    artifacts = repository.list_artifacts(outcome.command.id)

    assert outcome.command.result == {"metadata": {"password": "[REDACTED]"}}
    assert all(secret not in artifact.content for artifact in artifacts)
    assert secret.encode() not in database_path.read_bytes()


def test_devnet_deploy_creates_bound_pending_approval(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)
    executor = FakeExecutor()

    outcome = journal.execute(spec(tmp_path, program_id="Counter111"), executor, policy_context=context(tmp_path))
    approval = repository.require_approval(outcome.command.approval_id or "")
    decision = repository.require_policy_decision(approval.policy_decision_id)

    assert outcome.command.status == CommandStatus.APPROVAL_REQUIRED
    assert executor.call_count == 0
    assert approval.status == ApprovalStatus.PENDING
    assert approval.command_id == outcome.command.id
    assert approval.manifest["input_hash"] == decision.input_hash
    assert len(approval.manifest_hash) == 64


def test_approved_manifest_is_consumed_once_before_execution(tmp_path: Path) -> None:
    journal, repository, approvals, _ = governed_runtime(tmp_path)
    executor = FakeExecutor(ExecutionResult(exit_code=0, stdout="deployed"))
    waiting = journal.execute(spec(tmp_path), executor, policy_context=context(tmp_path)).command
    approval = approvals.approve(waiting.approval_id or "", approved_by="operator@example.com")

    outcome = journal.execute_approved(waiting.id, executor)
    events = repository.list_events(waiting.run_id)

    assert outcome.command.status == CommandStatus.SUCCEEDED
    assert repository.require_approval(approval.id).status == ApprovalStatus.CONSUMED
    assert executor.call_count == 1
    assert [event.event_type for event in events].index("approval.consumed") < [
        event.event_type for event in events
    ].index("command.running")


def test_unapproved_request_cannot_execute(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path)
    executor = FakeExecutor()
    waiting = journal.execute(spec(tmp_path), executor, policy_context=context(tmp_path)).command

    outcome = journal.execute_approved(waiting.id, executor)

    assert outcome.command.status == CommandStatus.REJECTED
    assert outcome.command.error == {"code": "approval_invalid", "message": "approval is not usable: pending"}
    assert repository.require_approval(waiting.approval_id or "").status == ApprovalStatus.PENDING
    assert executor.call_count == 0


def test_operator_denial_is_audited_and_blocks_execution(tmp_path: Path) -> None:
    journal, repository, approvals, _ = governed_runtime(tmp_path)
    executor = FakeExecutor()
    waiting = journal.execute(spec(tmp_path), executor, policy_context=context(tmp_path)).command

    denied = approvals.deny(waiting.approval_id or "", approved_by="operator", note="risk not accepted")
    outcome = journal.execute_approved(waiting.id, executor)

    assert denied.status == ApprovalStatus.DENIED
    assert outcome.command.status == CommandStatus.REJECTED
    assert executor.call_count == 0
    assert "approval.denied" in [event.event_type for event in repository.list_events(waiting.run_id)]


def test_command_mutation_invalidates_an_approved_manifest(tmp_path: Path) -> None:
    journal, repository, approvals, _ = governed_runtime(tmp_path)
    executor = FakeExecutor()
    waiting = journal.execute(spec(tmp_path), executor, policy_context=context(tmp_path)).command
    approvals.approve(waiting.approval_id or "", approved_by="operator")
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE commands SET arguments_json = ? WHERE id = ?",
            (json.dumps({"program_id": "DifferentProgram"}), waiting.id),
        )

    outcome = journal.execute_approved(waiting.id, executor)

    assert outcome.command.status == CommandStatus.REJECTED
    assert outcome.command.error == {
        "code": "approval_invalid",
        "message": "command or policy inputs changed after approval was requested",
    }
    assert executor.call_count == 0


def test_expired_approval_is_reconciled(tmp_path: Path) -> None:
    journal, repository, approvals, _ = governed_runtime(tmp_path)
    waiting = journal.execute(spec(tmp_path), FakeExecutor(), policy_context=context(tmp_path)).command
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE approvals SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", waiting.approval_id),
        )

    expired = approvals.expire_pending()

    assert [item.id for item in expired] == [waiting.approval_id]
    assert expired[0].status == ApprovalStatus.EXPIRED


def test_policy_snapshot_is_bound_to_run(tmp_path: Path) -> None:
    journal, repository, _, _ = governed_runtime(tmp_path, profile=PolicyProfile.DEVNET_SAFE)

    run = repository.require_run("run-policy")

    assert run.policy_snapshot is not None
    assert run.policy_snapshot["profile"] == "devnet-safe"
    assert run.policy_snapshot_hash == run.policy_snapshot["hash"]
