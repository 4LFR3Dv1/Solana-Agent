from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

SOLANA_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def hash_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MissionInputDefinition:
    name: str
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class PreconditionDefinition:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AcceptanceDefinition:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MissionStepDefinition:
    id: str
    adapter: str
    operation: str
    depends_on: tuple[str, ...] = ()
    arguments: dict[str, Any] = field(default_factory=dict)
    cwd: str = "{{runtime.workspace_root}}"
    timeout_seconds: int = 60
    preconditions: tuple[PreconditionDefinition, ...] = ()
    acceptance: tuple[AcceptanceDefinition, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "adapter": self.adapter,
            "operation": self.operation,
            "depends_on": list(self.depends_on),
            "arguments": self.arguments,
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "preconditions": [{"type": item.type, "parameters": item.parameters} for item in self.preconditions],
            "acceptance": [{"type": item.type, "parameters": item.parameters} for item in self.acceptance],
        }


@dataclass(frozen=True, slots=True)
class MissionDefinition:
    id: str
    version: str
    goal: str
    inputs: tuple[MissionInputDefinition, ...]
    steps: tuple[MissionStepDefinition, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "goal": self.goal,
            "inputs": [
                {
                    "name": item.name,
                    "required": item.required,
                    "default": item.default,
                    "description": item.description,
                }
                for item in self.inputs
            ],
            "steps": [step.to_dict() for step in self.steps],
        }

    @property
    def definition_hash(self) -> str:
        return hash_payload(self.to_dict())


@dataclass(frozen=True, slots=True)
class MissionPack:
    id: str
    version: str
    missions: dict[str, MissionDefinition]
    pack_hash: str


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    id: str
    version: str
    policy_profile: str
    workspace_root: str
    cluster: str | None = None
    wallet: str | None = None
    max_lamports: int | None = None
    tool_versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.policy_profile not in {"read-only", "local-safe", "devnet-safe"}:
            raise ValueError(f"unsupported policy profile: {self.policy_profile}")
        if self.max_lamports is not None and self.max_lamports < 0:
            raise ValueError("max_lamports must not be negative")
        if self.wallet is not None and not _valid_solana_public_key(self.wallet):
            raise ValueError("wallet must be a 32-byte base58 Solana public key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "policy_profile": self.policy_profile,
            "workspace_root": str(Path(self.workspace_root).resolve()),
            "cluster": self.cluster,
            "wallet": self.wallet,
            "max_lamports": self.max_lamports,
            "tool_versions": self.tool_versions,
        }

    @property
    def contract_hash(self) -> str:
        return hash_payload(self.to_dict())


class MissionStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


MISSION_STEP_TRANSITIONS: dict[MissionStepStatus, frozenset[MissionStepStatus]] = {
    MissionStepStatus.PENDING: frozenset(
        {
            MissionStepStatus.RUNNING,
            MissionStepStatus.FAILED,
            MissionStepStatus.BLOCKED,
            MissionStepStatus.SKIPPED,
        }
    ),
    MissionStepStatus.RUNNING: frozenset(
        {
            MissionStepStatus.WAITING_APPROVAL,
            MissionStepStatus.SUCCEEDED,
            MissionStepStatus.FAILED,
        }
    ),
    MissionStepStatus.WAITING_APPROVAL: frozenset({MissionStepStatus.SUCCEEDED, MissionStepStatus.FAILED}),
    MissionStepStatus.FAILED: frozenset({MissionStepStatus.RUNNING, MissionStepStatus.FAILED}),
    MissionStepStatus.BLOCKED: frozenset(
        {MissionStepStatus.RUNNING, MissionStepStatus.FAILED, MissionStepStatus.BLOCKED}
    ),
    MissionStepStatus.SUCCEEDED: frozenset(),
    MissionStepStatus.SKIPPED: frozenset(),
}


class InvalidMissionStepTransition(ValueError):
    def __init__(self, current: MissionStepStatus, target: MissionStepStatus) -> None:
        super().__init__(f"invalid mission step transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def require_mission_step_transition(current: MissionStepStatus, target: MissionStepStatus) -> None:
    if target not in MISSION_STEP_TRANSITIONS[current]:
        raise InvalidMissionStepTransition(current, target)


@dataclass(frozen=True, slots=True)
class MissionStepRecord:
    run_id: str
    step_id: str
    definition_hash: str
    status: MissionStepStatus
    attempt: int
    command_id: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: str
    updated_at: str


def _valid_solana_public_key(value: str) -> bool:
    if not value or any(character not in SOLANA_ALPHABET for character in value):
        return False
    number = 0
    for character in value:
        number = number * 58 + SOLANA_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    decoded = (b"\x00" * (len(value) - len(value.lstrip("1")))) + decoded
    return len(decoded) == 32
