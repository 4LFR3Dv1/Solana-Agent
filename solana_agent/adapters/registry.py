from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solana_agent.execution import Executor

from .anchor import AnchorAdapter
from .doctor import DoctorAdapter
from .filesystem import FilesystemAdapter
from .package_manager import PackageManagerAdapter
from .process import ProcessRunner
from .solana_cli import CLUSTER_URLS, SolanaCliAdapter
from .solana_rpc import SolanaRpcAdapter


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    workspace_root: Path
    cluster: str = "localnet"
    rpc_endpoint: str | None = None
    max_output_bytes: int = 1_000_000
    anchor_executable: str = "anchor"
    solana_executable: str = "solana"
    package_executable: str = "pnpm"


def build_adapter_registry(config: AdapterConfig) -> dict[str, Executor]:
    if config.cluster not in CLUSTER_URLS:
        raise ValueError("adapter cluster must be devnet, localnet, or localhost")
    endpoint = config.rpc_endpoint or CLUSTER_URLS[config.cluster]
    runner = ProcessRunner(max_output_bytes=config.max_output_bytes)
    return {
        "doctor": DoctorAdapter(),
        "filesystem": FilesystemAdapter(config.workspace_root),
        "anchor": AnchorAdapter(
            runner,
            executable=config.anchor_executable,
            package_manager=config.package_executable,
            default_cluster=config.cluster,
        ),
        "package": PackageManagerAdapter(runner, executable=config.package_executable),
        "solana": SolanaCliAdapter(
            runner,
            executable=config.solana_executable,
            default_cluster=config.cluster,
        ),
        "solana_rpc": SolanaRpcAdapter(endpoint),
    }
