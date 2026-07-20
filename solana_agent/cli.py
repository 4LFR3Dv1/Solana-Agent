from __future__ import annotations

import argparse
import json
from pathlib import Path

from .doctor import host_doctor
from .runtime import MissionRunner, RuntimeErrorWithContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solana-agent")
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Path to the Solana Agent repository root.",
    )
    parser.add_argument(
        "--shell-mode",
        choices=["auto", "native", "wsl"],
        default="auto",
        help="How external commands should be executed.",
    )
    parser.add_argument(
        "--wsl-distro",
        default=None,
        help="WSL distribution name to use when shell-mode is wsl.",
    )
    parser.add_argument(
        "--wsl-user",
        default=None,
        help="WSL user to use when shell-mode is wsl.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-env", help="Inspect the Solana environment.")
    inspect_parser.set_defaults(handler=handle_inspect_env)

    doctor_parser = subparsers.add_parser("doctor", help="Inspect host prerequisites.")
    doctor_parser.set_defaults(handler=handle_doctor)

    run_parser = subparsers.add_parser("run", help="Run a supported mission.")
    run_subparsers = run_parser.add_subparsers(dest="mission_name", required=True)

    counter_parser = run_subparsers.add_parser("create-counter", help="Run the create-counter mission.")
    counter_parser.add_argument("--workspace", required=True, type=Path, help="Target workspace path.")
    counter_parser.add_argument("--project-name", help="Anchor project name. Defaults to the workspace name.")
    counter_parser.add_argument("--cluster", default="devnet", help="Target Solana cluster.")
    counter_parser.add_argument(
        "--airdrop-amount",
        default="1",
        help="Amount to request from the devnet faucet when airdrop approval is present.",
    )
    counter_parser.add_argument(
        "--approve-airdrop",
        action="store_true",
        help="Approve a devnet airdrop during the mission.",
    )
    counter_parser.add_argument(
        "--approve-deploy",
        action="store_true",
        help="Approve the devnet deploy step.",
    )
    counter_parser.add_argument(
        "--skip-airdrop",
        action="store_true",
        help="Skip the airdrop step even if approved.",
    )
    counter_parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip deploy and invoke. Useful for validating the scaffold and test flow only.",
    )
    counter_parser.set_defaults(handler=handle_create_counter)

    return parser


def handle_inspect_env(args: argparse.Namespace) -> int:
    runner = MissionRunner(
        repo_root=args.repo_root,
        shell_mode=args.shell_mode,
        wsl_distro=args.wsl_distro,
        wsl_user=args.wsl_user,
    )
    result = runner.inspect_env()
    print(json.dumps(result, indent=2))
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    print(json.dumps(host_doctor(), indent=2))
    return 0


def handle_create_counter(args: argparse.Namespace) -> int:
    runner = MissionRunner(
        repo_root=args.repo_root,
        shell_mode=args.shell_mode,
        wsl_distro=args.wsl_distro,
        wsl_user=args.wsl_user,
    )
    result = runner.run_create_counter(
        workspace=args.workspace,
        project_name=args.project_name,
        cluster=args.cluster,
        airdrop_amount=args.airdrop_amount,
        approve_airdrop=args.approve_airdrop,
        approve_deploy=args.approve_deploy,
        skip_airdrop=args.skip_airdrop,
        skip_deploy=args.skip_deploy,
    )
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except RuntimeErrorWithContext as exc:
        print(json.dumps(exc.to_dict(), indent=2))
        return 1
