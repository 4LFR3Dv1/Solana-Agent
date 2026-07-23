from __future__ import annotations

import json
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


def test_parser_exposes_declarative_mission_catalog() -> None:
    args = build_parser().parse_args(["missions", "show", "create-counter"])

    assert args.missions_command == "show"
    assert args.mission_name == "create-counter"
    assert callable(args.handler)


def test_cli_validates_core_mission_pack() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "solana_agent", "missions", "validate"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["mission_count"] == 3


def test_parser_exposes_governed_start_resume_and_approval_commands(tmp_path: Path) -> None:
    contract = tmp_path / "runtime.json"
    start = build_parser().parse_args(
        ["missions", "start", "create-counter", "--contract", str(contract), "--input", "project_name=counter"]
    )
    resume = build_parser().parse_args(["missions", "resume", "run-1", "--contract", str(contract)])
    approve = build_parser().parse_args(
        ["approvals", "approve", "approval-1", "--by", "operator", "--contract", str(contract)]
    )

    assert start.mission_name == "create-counter"
    assert start.input == ["project_name=counter"]
    assert resume.run_id == "run-1"
    assert approve.approval_decision == "approve"


def test_parser_exposes_failed_command_output_inspection(tmp_path: Path) -> None:
    contract = tmp_path / "runtime.json"
    args = build_parser().parse_args(
        [
            "commands",
            "list",
            "run-1",
            "--contract",
            str(contract),
            "--failed-only",
            "--include-output",
        ]
    )

    assert args.commands_command == "list"
    assert args.run_id == "run-1"
    assert args.failed_only is True
    assert args.include_output is True
