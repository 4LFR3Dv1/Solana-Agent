from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from solana_agent.execution import Executor
from solana_agent.storage import JournalRepository

from .anchor import AnchorAdapter
from .counter_template import CounterTemplateAdapter
from .doctor import DoctorAdapter
from .evidence import EvidenceAdapter
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
    template_root: Path | None = None
    repository: JournalRepository | None = None


def build_adapter_registry(config: AdapterConfig) -> dict[str, Executor]:
    if config.cluster not in CLUSTER_URLS:
        raise ValueError("adapter cluster must be devnet, localnet, or localhost")
    endpoint = config.rpc_endpoint or CLUSTER_URLS[config.cluster]
    runner = ProcessRunner(max_output_bytes=config.max_output_bytes)
    source_templates = Path(__file__).resolve().parents[2] / "templates" / "anchor-counter" / "files"
    installed_templates = Path(sys.prefix) / "share" / "solana-agent" / "templates" / "anchor-counter" / "files"
    template_root = config.template_root or (source_templates if source_templates.is_dir() else installed_templates)
    registry: dict[str, Executor] = {
        "doctor": DoctorAdapter(),
        "filesystem": FilesystemAdapter(config.workspace_root),
        "counter_template": CounterTemplateAdapter(config.workspace_root, template_root),
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
    if config.repository is not None:
        registry["evidence"] = EvidenceAdapter(config.repository, config.workspace_root, endpoint)
    return registry
