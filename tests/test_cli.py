from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from solana_agent.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_module_help_is_available_without_solana_toolchain() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "solana_agent", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Solana environment" in completed.stdout
    assert "create-counter" not in completed.stderr


def test_parser_exposes_doctor_command() -> None:
    args = build_parser().parse_args(["doctor"])

    assert args.command == "doctor"
    assert callable(args.handler)


def test_parser_reads_create_counter_inputs(tmp_path: Path) -> None:
    workspace = tmp_path / "counter-demo"
    args = build_parser().parse_args(
        [
            "--shell-mode",
            "native",
            "run",
            "create-counter",
            "--workspace",
            str(workspace),
            "--project-name",
            "counter-demo",
            "--skip-deploy",
        ]
    )

    assert args.shell_mode == "native"
    assert args.mission_name == "create-counter"
    assert args.workspace == workspace
    assert args.project_name == "counter-demo"
    assert args.skip_deploy is True


def test_parser_requires_a_mission_for_run() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["run"])

    assert exc_info.value.code == 2
