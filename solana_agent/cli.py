from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .contracts import RuntimeContract
from .doctor import host_doctor
from .missions import (
    MissionLoadError,
    load_mission_pack,
    load_runtime_contract,
    topological_steps,
)
from .missions.engine import MissionExecutionError, MissionOutcome
from .runtime import MissionRunner, RuntimeErrorWithContext
from .runtime_factory import GovernedRuntime, build_governed_runtime


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
    missions_start = missions_subparsers.add_parser("start", help="Start a governed declarative mission.")
    missions_start.add_argument("mission_name")
    _add_governed_arguments(missions_start)
    missions_start.add_argument(
        "--input", action="append", default=[], metavar="KEY=VALUE", help="Mission input; may be repeated."
    )
    missions_start.add_argument("--run-id", default=None)
    missions_start.set_defaults(handler=handle_missions_start)
    missions_resume = missions_subparsers.add_parser("resume", help="Resume a persisted governed mission.")
    missions_resume.add_argument("run_id")
    _add_governed_arguments(missions_resume)
    missions_resume.set_defaults(handler=handle_missions_resume)

    approvals_parser = subparsers.add_parser("approvals", help="Inspect and decide bound approvals.")
    approvals_subparsers = approvals_parser.add_subparsers(dest="approvals_command", required=True)
    approvals_list = approvals_subparsers.add_parser("list", help="List approval records for a run.")
    approvals_list.add_argument("run_id")
    _add_governed_arguments(approvals_list)
    approvals_list.set_defaults(handler=handle_approvals_list)
    for decision in ("approve", "deny"):
        decision_parser = approvals_subparsers.add_parser(decision, help=f"{decision.title()} a pending request.")
        decision_parser.add_argument("approval_id")
        decision_parser.add_argument("--by", required=True, dest="decided_by")
        decision_parser.add_argument("--note", default=None)
        _add_governed_arguments(decision_parser)
        decision_parser.set_defaults(handler=handle_approval_decision, approval_decision=decision)

    commands_parser = subparsers.add_parser("commands", help="Inspect the persisted command journal.")
    commands_subparsers = commands_parser.add_subparsers(dest="commands_command", required=True)
    commands_list = commands_subparsers.add_parser("list", help="List commands recorded for a run.")
    commands_list.add_argument("run_id")
    commands_list.add_argument("--failed-only", action="store_true")
    commands_list.add_argument("--include-output", action="store_true")
    _add_governed_arguments(commands_list)
    commands_list.set_defaults(handler=handle_commands_list)

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


def _add_governed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", required=True, type=Path, help="Runtime contract JSON or YAML.")
    parser.add_argument(
        "--state-root", type=Path, default=Path(".solana-agent/state"), help="Persistent runtime state directory."
    )


def _governed_runtime(args: argparse.Namespace) -> tuple[RuntimeContract, GovernedRuntime]:
    contract = load_runtime_contract(args.contract.resolve())
    state_root = args.state_root if args.state_root.is_absolute() else args.repo_root.resolve() / args.state_root
    return contract, build_governed_runtime(state_root, contract)


def _mission_outcome(outcome: MissionOutcome) -> dict[str, object]:
    return asdict(outcome)


def _parse_inputs(values: list[str]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for item in values:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"mission input must use KEY=VALUE: {item!r}")
        try:
            value: object = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        parsed[key.strip()] = value
    return parsed


def handle_missions_start(args: argparse.Namespace) -> int:
    contract, runtime = _governed_runtime(args)
    pack = load_mission_pack(_mission_pack_root(args.repo_root))
    outcome = runtime.engine.start(
        pack,
        args.mission_name,
        inputs=_parse_inputs(args.input),
        runtime_contract=contract,
        run_id=args.run_id,
    )
    print(json.dumps(_mission_outcome(outcome), indent=2, default=str))
    return 0 if outcome.status in {"completed", "waiting_approval"} else 1


def handle_missions_resume(args: argparse.Namespace) -> int:
    contract, runtime = _governed_runtime(args)
    pack = load_mission_pack(_mission_pack_root(args.repo_root))
    run = runtime.repository.require_run(args.run_id)
    inputs = run.metadata.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError("persisted run inputs are invalid")
    outcome = runtime.engine.resume(pack, args.run_id, inputs=inputs, runtime_contract=contract)
    print(json.dumps(_mission_outcome(outcome), indent=2, default=str))
    return 0 if outcome.status in {"completed", "waiting_approval"} else 1


def handle_approvals_list(args: argparse.Namespace) -> int:
    _, runtime = _governed_runtime(args)
    runtime.repository.require_run(args.run_id)
    records = [
        asdict(approval)
        for command in runtime.repository.list_commands(args.run_id)
        for approval in runtime.repository.list_approvals(command.id)
    ]
    print(json.dumps({"run_id": args.run_id, "approvals": records}, indent=2, default=str))
    return 0


def handle_approval_decision(args: argparse.Namespace) -> int:
    _, runtime = _governed_runtime(args)
    method = runtime.approvals.approve if args.approval_decision == "approve" else runtime.approvals.deny
    record = method(args.approval_id, approved_by=args.decided_by, note=args.note)
    print(json.dumps(asdict(record), indent=2, default=str))
    return 0


def handle_commands_list(args: argparse.Namespace) -> int:
    _, runtime = _governed_runtime(args)
    runtime.repository.require_run(args.run_id)
    records: list[dict[str, object]] = []
    for command in runtime.repository.list_commands(args.run_id):
        if args.failed_only and command.status.value not in {
            "failed",
            "rejected",
            "cancelled",
            "interrupted",
            "timed_out",
        }:
            continue
        record = asdict(command)
        if args.include_output:
            record["stdout"] = (
                runtime.repository.require_artifact(command.stdout_artifact_id).content
                if command.stdout_artifact_id
                else ""
            )
            record["stderr"] = (
                runtime.repository.require_artifact(command.stderr_artifact_id).content
                if command.stderr_artifact_id
                else ""
            )
        records.append(record)
    print(json.dumps({"run_id": args.run_id, "commands": records}, indent=2, default=str))
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
    except (MissionExecutionError, MissionLoadError, KeyError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, indent=2))
        return 1
