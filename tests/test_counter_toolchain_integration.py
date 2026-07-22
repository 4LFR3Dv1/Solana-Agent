from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from solana_agent.adapters import AnchorAdapter, CounterTemplateAdapter, LocalValidator, PackageManagerAdapter
from solana_agent.execution import ExecutionRequest


def request(adapter: str, operation: str, cwd: Path, timeout: int = 300, **arguments: object) -> ExecutionRequest:
    return ExecutionRequest("command-integration", adapter, operation, dict(arguments), str(cwd), timeout)


@pytest.mark.integration
def test_counter_template_builds_and_passes_anchor_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("SOLANA_AGENT_RUN_INTEGRATION") != "1":
        pytest.skip("set SOLANA_AGENT_RUN_INTEGRATION=1 inside the pinned toolchain container")
    required = ("anchor", "pnpm", "solana", "solana-test-validator")
    if any(shutil.which(executable) is None for executable in required):
        pytest.skip("pinned Solana/Anchor toolchain is not installed")

    workspace = tmp_path / "counter-proof"
    templates = Path(__file__).resolve().parents[1] / "templates" / "anchor-counter" / "files"
    wallet = tmp_path / "operator.json"
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/solana/create_ephemeral_keypair.py"), str(wallet)],
        check=True,
        text=True,
        capture_output=True,
    )
    monkeypatch.setenv("ANCHOR_WALLET", str(wallet))
    monkeypatch.setenv("ANCHOR_PROVIDER_URL", "http://127.0.0.1:8899")

    scaffold = AnchorAdapter(default_cluster="localnet").execute(
        request("anchor", "scaffold", tmp_path, workspace=str(workspace), project_name="counter-proof")
    )
    assert scaffold.exit_code == 0, scaffold.stderr
    applied = CounterTemplateAdapter(tmp_path, templates).execute(
        request(
            "counter_template",
            "apply",
            workspace,
            workspace=str(workspace),
            project_name="counter-proof",
            cluster="localnet",
        )
    )
    assert applied.exit_code == 0, applied.stderr
    installed = PackageManagerAdapter().execute(request("package", "install", workspace, timeout=600))
    assert installed.exit_code == 0, installed.stderr
    built = AnchorAdapter(default_cluster="localnet").execute(request("anchor", "build", workspace, timeout=900))
    assert built.exit_code == 0, built.stderr

    with LocalValidator(tmp_path / "ledger"):
        airdrop = subprocess.run(
            ["solana", "airdrop", "10", "--keypair", str(wallet), "--url", "http://127.0.0.1:8899"],
            check=False,
            text=True,
            capture_output=True,
        )
        assert airdrop.returncode == 0, airdrop.stderr
        tested = AnchorAdapter(default_cluster="localnet").execute(
            request("anchor", "test", workspace, timeout=900, skip_local_validator=True)
        )
    assert tested.exit_code == 0, tested.stderr
