from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solana_agent.adapters import AdapterConfig, build_adapter_registry
from solana_agent.authority import ApprovalService
from solana_agent.contracts import RuntimeContract
from solana_agent.missions import MissionEngine
from solana_agent.storage import Database, JournalRepository


@dataclass(frozen=True, slots=True)
class GovernedRuntime:
    database: Database
    repository: JournalRepository
    approvals: ApprovalService
    engine: MissionEngine


def build_governed_runtime(state_root: Path, contract: RuntimeContract) -> GovernedRuntime:
    """Build a persistent mission runtime backed by the production adapter registry."""

    database = Database(state_root.resolve() / "runtime.db")
    database.initialize()
    repository = JournalRepository(database)
    approvals = ApprovalService(repository)
    executors = build_adapter_registry(
        AdapterConfig(
            workspace_root=Path(contract.workspace_root),
            cluster=contract.cluster or "localnet",
            repository=repository,
        )
    )
    return GovernedRuntime(
        database=database,
        repository=repository,
        approvals=approvals,
        engine=MissionEngine(repository, approvals, executors),
    )
