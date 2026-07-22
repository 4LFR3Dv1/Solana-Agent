from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solana_agent.adapters import (
    AdapterConfig,
    AnchorAdapter,
    PackageManagerAdapter,
    SolanaCliAdapter,
    SolanaRpcAdapter,
    build_adapter_registry,
)
from solana_agent.contracts import RuntimeContract
from solana_agent.execution import ExecutionRequest, ExecutionResult
from solana_agent.runtime_factory import build_governed_runtime


class RecordingRunner:
    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.calls: list[tuple[list[str], Path, int]] = []
        self.result = result or ExecutionResult(0, stdout="ok", metadata={"shell": False})

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        environment: dict[str, str] | None = None,
    ) -> ExecutionResult:
        del environment
        self.calls.append((argv, cwd, timeout_seconds))
        return self.result


class RecordingTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def call(self, endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        self.calls.append((endpoint, payload, timeout_seconds))
        return self.response


def request(adapter: str, operation: str, cwd: Path, **arguments: object) -> ExecutionRequest:
    return ExecutionRequest("command-1", adapter, operation, dict(arguments), str(cwd), 30)


def test_anchor_adapter_generates_real_scaffold_build_test_and_deploy_argv(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = AnchorAdapter(runner, default_cluster="localnet")  # type: ignore[arg-type]
    workspace = tmp_path / "counter"

    adapter.execute(request("anchor", "scaffold", tmp_path, workspace=str(workspace), project_name="counter"))
    workspace.mkdir()
    adapter.execute(request("anchor", "build", workspace, verifiable=True))
    adapter.execute(request("anchor", "test", workspace, skip_local_validator=True))
    adapter.execute(request("anchor", "deploy", workspace))

    assert [call[0] for call in runner.calls] == [
        ["anchor", "init", "counter", "--package-manager", "pnpm"],
        ["anchor", "build", "--verifiable"],
        ["anchor", "test", "--skip-local-validator"],
        ["anchor", "deploy", "--provider.cluster", "localnet"],
    ]


def test_package_adapter_uses_frozen_pnpm_lock(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    runner = RecordingRunner()
    adapter = PackageManagerAdapter(runner)  # type: ignore[arg-type]

    adapter.execute(request("package", "install", tmp_path))

    assert runner.calls[0][0] == ["pnpm", "install", "--ignore-scripts", "--frozen-lockfile"]


def test_solana_cli_inherits_allowlisted_cluster_and_rejects_mainnet(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = SolanaCliAdapter(runner, default_cluster="localnet")  # type: ignore[arg-type]
    adapter.execute(request("solana", "balance", tmp_path))

    assert runner.calls[0][0] == [
        "solana",
        "balance",
        "--url",
        "http://127.0.0.1:8899",
        "--output",
        "json",
    ]
    with pytest.raises(ValueError, match="cluster must be"):
        adapter.execute(request("solana", "balance", tmp_path, cluster="mainnet-beta"))


def test_solana_cli_requires_minimum_prefunded_balance(tmp_path: Path) -> None:
    funded_runner = RecordingRunner(ExecutionResult(0, stdout="2000000000 lamports\n", metadata={"shell": False}))
    funded = SolanaCliAdapter(funded_runner, default_cluster="devnet")  # type: ignore[arg-type]
    result = funded.execute(
        request(
            "solana",
            "require_balance",
            tmp_path,
            wallet="F1K3nPb4JcZ7nd6yEpWtspbCoiJzo1bL7tnUNF6SfHcp",
            minimum_lamports=2_000_000_000,
        )
    )

    assert result.exit_code == 0
    assert result.metadata["balance_lamports"] == 2_000_000_000
    assert "--lamports" in funded_runner.calls[0][0]

    empty_runner = RecordingRunner(ExecutionResult(0, stdout="0 lamports\n", metadata={"shell": False}))
    empty = SolanaCliAdapter(empty_runner, default_cluster="devnet")  # type: ignore[arg-type]
    rejected = empty.execute(
        request(
            "solana",
            "require_balance",
            tmp_path,
            wallet="F1K3nPb4JcZ7nd6yEpWtspbCoiJzo1bL7tnUNF6SfHcp",
            minimum_lamports=2_000_000_000,
        )
    )

    assert rejected.exit_code == 1
    assert "below required minimum" in rejected.stderr


def test_rpc_adapter_builds_json_rpc_and_surfaces_rpc_errors(tmp_path: Path) -> None:
    transport = RecordingTransport({"jsonrpc": "2.0", "id": "command-1", "result": "ok"})
    adapter = SolanaRpcAdapter("http://127.0.0.1:8899", transport)

    result = adapter.execute(request("solana_rpc", "get_health", tmp_path))

    assert result.exit_code == 0
    assert transport.calls[0][1]["method"] == "getHealth"
    assert transport.calls[0][1]["params"] == []


def test_rpc_adapter_rejects_untrusted_endpoint() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        SolanaRpcAdapter("https://rpc.example.com")


def test_registry_and_runtime_factory_connect_production_adapters(tmp_path: Path) -> None:
    registry = build_adapter_registry(AdapterConfig(workspace_root=tmp_path, cluster="localnet"))
    contract = RuntimeContract(
        id="test-runtime",
        version="1",
        policy_profile="local-safe",
        workspace_root=str(tmp_path),
        cluster="localnet",
    )
    runtime = build_governed_runtime(tmp_path / ".state", contract)

    assert set(registry) == {
        "doctor",
        "filesystem",
        "counter_template",
        "anchor",
        "package",
        "solana",
        "solana_rpc",
    }
    assert set(runtime.engine.executors) == {*registry, "evidence"}
    assert runtime.database.path.is_file()
