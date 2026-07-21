from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .doctor import host_doctor
from .missions import load_mission_pack, topological_steps
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

    missions_parser = subparsers.add_parser("missions", help="Inspect declarative mission packs.")
    missions_subparsers = missions_parser.add_subparsers(dest="missions_command", required=True)
    missions_list = missions_subparsers.add_parser("list", help="List declarative missions.")
    missions_list.set_defaults(handler=handle_missions_list)
    missions_show = missions_subparsers.add_parser("show", help="Show a declarative mission.")
    missions_show.add_argument("mission_name", help="Mission identifier from the core pack.")
    missions_show.set_defaults(handler=handle_missions_show)
    missions_validate = missions_subparsers.add_parser("validate", help="Validate the core mission pack.")
    missions_validate.set_defaults(handler=handle_missions_validate)

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


def handle_missions_list(args: argparse.Namespace) -> int:
    pack = load_mission_pack(_mission_pack_root(args.repo_root))
    payload = {
        "pack": {"id": pack.id, "version": pack.version, "hash": pack.pack_hash},
        "missions": [
            {
                "id": mission.id,
                "version": mission.version,
                "hash": mission.definition_hash,
                "steps": len(mission.steps),
            }
            for mission in pack.missions.values()
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def handle_missions_show(args: argparse.Namespace) -> int:
    pack = load_mission_pack(_mission_pack_root(args.repo_root))
    try:
        mission = pack.missions[args.mission_name]
    except KeyError as exc:
        raise RuntimeErrorWithContext(
            "Mission is not present in the declarative pack.",
            mission_name=args.mission_name,
            available=sorted(pack.missions),
        ) from exc
    print(
        json.dumps(
            {
                **mission.to_dict(),
                "definition_hash": mission.definition_hash,
                "pack_hash": pack.pack_hash,
                "execution_order": [step.id for step in topological_steps(mission)],
            },
            indent=2,
        )
    )
    return 0


def handle_missions_validate(args: argparse.Namespace) -> int:
    pack = load_mission_pack(_mission_pack_root(args.repo_root))
    print(
        json.dumps(
            {
                "ok": True,
                "pack_id": pack.id,
                "pack_version": pack.version,
                "pack_hash": pack.pack_hash,
                "mission_count": len(pack.missions),
                "missions": sorted(pack.missions),
            },
            indent=2,
        )
    )
    return 0


def _mission_pack_root(repo_root: Path) -> Path:
    repository_pack = repo_root.resolve() / "missions"
    if (repository_pack / "mission-pack.json").is_file():
        return repository_pack
    installed_pack = Path(sys.prefix) / "share" / "solana-agent" / "missions"
    if (installed_pack / "mission-pack.json").is_file():
        return installed_pack
    raise RuntimeErrorWithContext(
        "Declarative mission pack was not found.",
        searched=[str(repository_pack), str(installed_pack)],
    )


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
