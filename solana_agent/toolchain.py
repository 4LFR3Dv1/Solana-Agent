from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolRequirement:
    name: str
    version: str
    command: tuple[str, ...]
    version_pattern: str


@dataclass(frozen=True, slots=True)
class ToolProbe:
    name: str
    expected_version: str
    available: bool
    compatible: bool
    output: str
    path: str
    remediation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expected_version": self.expected_version,
            "available": self.available,
            "compatible": self.compatible,
            "output": self.output,
            "path": self.path,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ToolchainLock:
    schema_version: int
    environment: dict[str, str]
    tools: dict[str, ToolRequirement]

    @classmethod
    def load(cls, path: Path) -> ToolchainLock:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError("unsupported toolchain lock schema")
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, dict) or not raw_tools:
            raise ValueError("toolchain lock must define tools")
        tools: dict[str, ToolRequirement] = {}
        for name, raw in raw_tools.items():
            if not isinstance(raw, dict):
                raise ValueError(f"tool requirement must be an object: {name}")
            command = raw.get("command")
            if not isinstance(command, list) or not command:
                raise ValueError(f"tool command must be a non-empty list: {name}")
            tools[str(name)] = ToolRequirement(
                name=str(name),
                version=str(raw["version"]),
                command=tuple(str(item) for item in command),
                version_pattern=str(raw["version_pattern"]),
            )
        environment = payload.get("environment")
        if not isinstance(environment, dict):
            raise ValueError("toolchain environment must be an object")
        return cls(
            schema_version=1,
            environment={str(key): str(value) for key, value in environment.items()},
            tools=tools,
        )


def probe_toolchain(lock: ToolchainLock) -> list[ToolProbe]:
    return [probe_requirement(requirement) for requirement in lock.tools.values()]


def probe_requirement(requirement: ToolRequirement) -> ToolProbe:
    executable = shutil.which(requirement.command[0])
    if executable is None:
        return ToolProbe(
            name=requirement.name,
            expected_version=requirement.version,
            available=False,
            compatible=False,
            output="",
            path="",
            remediation=_remediation(requirement.name, requirement.version),
        )
    try:
        completed = subprocess.run(
            list(requirement.command),
            shell=False,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        lines = (completed.stdout or completed.stderr).strip().splitlines()
        output = lines[0] if lines else ""
        compatible = completed.returncode == 0 and requirement.version_pattern in output
    except (OSError, subprocess.SubprocessError) as exc:
        output = str(exc)
        compatible = False
    return ToolProbe(
        name=requirement.name,
        expected_version=requirement.version,
        available=True,
        compatible=compatible,
        output=output,
        path=executable,
        remediation="" if compatible else _remediation(requirement.name, requirement.version),
    )


def _remediation(name: str, version: str) -> str:
    commands = {
        "python": f"Install Python {version}",
        "rustc": f"rustup toolchain install {version} && rustup default {version}",
        "cargo": f"rustup toolchain install {version} && rustup default {version}",
        "solana": f"Install Agave/Solana CLI v{version} from https://release.anza.xyz/v{version}/install",
        "solana-test-validator": f"Install Agave/Solana CLI v{version}",
        "anchor": f"cargo install --git https://github.com/otter-sec/anchor --tag v{version} anchor-cli --locked",
        "node": f"Install Node.js v{version}",
        "pnpm": f"corepack prepare pnpm@{version} --activate",
    }
    return commands.get(name, f"Install {name} {version}")
