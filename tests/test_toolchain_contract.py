from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from solana_agent.toolchain import ToolchainLock, ToolRequirement, probe_requirement

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repository_toolchain_lock_is_complete() -> None:
    lock = ToolchainLock.load(REPO_ROOT / "toolchain.lock.json")

    assert lock.environment["python"] == "3.11.9"
    assert set(lock.tools) == {
        "python",
        "rustc",
        "cargo",
        "solana",
        "solana-test-validator",
        "anchor",
        "node",
        "pnpm",
        "devnet-pow",
    }


def test_tool_probe_reports_exact_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    requirement = ToolRequirement("anchor", "1.1.2", ("anchor", "--version"), "anchor-cli 1.1.2")
    monkeypatch.setattr("shutil.which", lambda command: f"/tools/{command}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "anchor-cli 1.1.2\n", ""),
    )

    probe = probe_requirement(requirement)

    assert probe.compatible is True
    assert probe.path == "/tools/anchor"
    assert probe.remediation == ""
