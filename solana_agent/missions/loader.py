from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from solana_agent.contracts.mission import (
    AcceptanceDefinition,
    MissionDefinition,
    MissionInputDefinition,
    MissionPack,
    MissionStepDefinition,
    PreconditionDefinition,
    RuntimeContract,
    hash_payload,
)

from .graph import validate_mission_graph


class MissionLoadError(ValueError):
    pass


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MissionLoadError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _load_document(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise MissionLoadError(f"unsupported document format: {path.suffix}")
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise MissionLoadError(f"failed to load {path}: {exc}") from exc
    return _mapping(value, str(path))


def load_mission(path: Path) -> MissionDefinition:
    payload = _load_document(path)
    try:
        inputs = tuple(
            MissionInputDefinition(
                name=str(item["name"]),
                required=bool(item.get("required", True)),
                default=item.get("default"),
                description=str(item.get("description", "")),
            )
            for raw in payload.get("inputs", [])
            for item in [_mapping(raw, "mission input")]
        )
        steps = tuple(_parse_step(raw) for raw in payload["steps"])
        mission = MissionDefinition(
            id=str(payload["id"]),
            version=str(payload["version"]),
            goal=str(payload["goal"]),
            inputs=inputs,
            steps=steps,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionLoadError(f"invalid mission definition in {path}: {exc}") from exc
    if len({item.name for item in mission.inputs}) != len(mission.inputs):
        raise MissionLoadError("mission input names must be unique")
    validate_mission_graph(mission)
    return mission


def _parse_step(raw: Any) -> MissionStepDefinition:
    item = _mapping(raw, "mission step")
    return MissionStepDefinition(
        id=str(item["id"]),
        adapter=str(item["adapter"]),
        operation=str(item["operation"]),
        depends_on=tuple(str(value) for value in item.get("depends_on", [])),
        arguments=_mapping(item.get("arguments", {}), "step arguments"),
        cwd=str(item.get("cwd", "{{runtime.workspace_root}}")),
        timeout_seconds=int(item.get("timeout_seconds", 60)),
        preconditions=tuple(_parse_check(raw_check, "precondition") for raw_check in item.get("preconditions", [])),
        acceptance=tuple(_parse_acceptance(raw_check) for raw_check in item.get("acceptance", [])),
    )


def _parse_check(raw: Any, label: str) -> PreconditionDefinition:
    item = _mapping(raw, label)
    return PreconditionDefinition(
        type=str(item["type"]),
        parameters=_mapping(item.get("parameters", {}), f"{label} parameters"),
    )


def _parse_acceptance(raw: Any) -> AcceptanceDefinition:
    item = _mapping(raw, "acceptance")
    return AcceptanceDefinition(
        type=str(item["type"]),
        parameters=_mapping(item.get("parameters", {}), "acceptance parameters"),
    )


def load_mission_pack(root: Path) -> MissionPack:
    root = root.resolve()
    manifest_path = root / "mission-pack.json"
    manifest = _load_document(manifest_path)
    mission_files = manifest.get("missions")
    if not isinstance(mission_files, list) or not mission_files:
        raise MissionLoadError("mission pack must list at least one mission")
    missions: dict[str, MissionDefinition] = {}
    for relative in mission_files:
        mission_path = (root / str(relative)).resolve()
        if mission_path != root and root not in mission_path.parents:
            raise MissionLoadError(f"mission path escapes pack root: {relative}")
        mission = load_mission(mission_path)
        if mission.id in missions:
            raise MissionLoadError(f"duplicate mission id in pack: {mission.id}")
        missions[mission.id] = mission
    pack_id = str(manifest["id"])
    pack_version = str(manifest["version"])
    if not pack_id.strip() or not pack_version.strip():
        raise MissionLoadError("mission pack id and version must not be empty")
    pack_payload: dict[str, Any] = {
        "id": pack_id,
        "version": pack_version,
        "missions": {mission_id: mission.to_dict() for mission_id, mission in sorted(missions.items())},
    }
    return MissionPack(
        id=pack_id,
        version=pack_version,
        missions=missions,
        pack_hash=hash_payload(pack_payload),
    )


def load_runtime_contract(path: Path) -> RuntimeContract:
    payload = _load_document(path)
    try:
        return RuntimeContract(
            id=str(payload["id"]),
            version=str(payload["version"]),
            policy_profile=str(payload["policy_profile"]),
            workspace_root=str(payload["workspace_root"]),
            cluster=str(payload["cluster"]) if payload.get("cluster") is not None else None,
            wallet=str(payload["wallet"]) if payload.get("wallet") is not None else None,
            max_lamports=int(payload["max_lamports"]) if payload.get("max_lamports") is not None else None,
            tool_versions={
                str(key): str(value)
                for key, value in _mapping(payload.get("tool_versions", {}), "tool_versions").items()
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionLoadError(f"invalid runtime contract in {path}: {exc}") from exc
