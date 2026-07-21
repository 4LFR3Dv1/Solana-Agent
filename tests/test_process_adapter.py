from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from solana_agent.adapters import ProcessRunner
from solana_agent.execution import CommandTimedOut


def test_process_runner_uses_structured_argv_without_a_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ProcessRunner().run(["solana", "--version"], cwd=tmp_path, timeout_seconds=5)

    assert observed["argv"] == ["solana", "--version"]
    assert observed["shell"] is False
    assert observed["capture_output"] is True
    assert result.metadata["shell"] is False


def test_process_runner_caps_persisted_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="x" * 100, stderr=""),
    )

    result = ProcessRunner(max_output_bytes=64).run(["tool"], cwd=tmp_path, timeout_seconds=5)

    assert len(result.stdout.encode()) <= 64
    assert result.metadata["stdout_truncated"] is True


def test_process_runner_converts_timeout_to_runtime_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["tool"], 1, output="partial")

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(CommandTimedOut, match="exceeded 1 seconds") as captured:
        ProcessRunner().run(["tool"], cwd=tmp_path, timeout_seconds=1)
    assert captured.value.stdout == "partial"
