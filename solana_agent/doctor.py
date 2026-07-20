from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class HostCommandStatus:
    name: str
    available: bool
    version: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "version": self.version,
            "path": self.path,
        }


def _run_version(command: str) -> tuple[bool, str]:
    path = shutil.which(command)
    if not path:
        return False, ""
    completed = subprocess.run(
        [command, "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return True, output[0] if output else ""


def _run_text(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def host_doctor() -> dict[str, Any]:
    commands = []
    for name in ["solana", "anchor", "rustc", "node", "yarn"]:
        available, version = _run_version(name)
        commands.append(
            HostCommandStatus(
                name=name,
                available=available,
                version=version,
                path=shutil.which(name) or "",
            ).to_dict()
        )

    wsl = {
        "supported": os.name == "nt",
        "installed": False,
        "default_version": "",
        "distributions": [],
    }
    if os.name == "nt":
        status_code, status_out, status_err = _run_text(["wsl.exe", "--status"])
        list_code, list_out, list_err = _run_text(["wsl.exe", "--list", "--quiet"])
        wsl["installed"] = list_code == 0 and bool(list_out.strip())
        status_text = status_out or status_err
        for line in status_text.splitlines():
            if "Vers" in line or "Version" in line:
                wsl["default_version"] = line
                break
        wsl["distributions"] = [line.strip() for line in list_out.splitlines() if line.strip()]
        wsl["status_error"] = status_err

    missing_runtime = []
    if os.name == "nt" and not wsl["installed"]:
        missing_runtime.append("Ubuntu on WSL")
    if not any(command["name"] == "node" and command["available"] for command in commands):
        missing_runtime.append("node")

    return {
        "ok": True,
        "host_os": os.name,
        "wsl": wsl,
        "commands": commands,
        "ready_for_runtime": not missing_runtime,
        "missing_runtime": missing_runtime,
        "next_steps": _next_steps(os.name, wsl, commands),
    }


def _next_steps(host_os: str, wsl: dict[str, Any], commands: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    if host_os == "nt" and not wsl["installed"]:
        steps.append("Install Ubuntu in WSL: wsl.exe --install Ubuntu")
    if host_os == "nt" and wsl["installed"]:
        steps.append("Open Ubuntu and install Solana CLI, Rust, Anchor, Node, and Yarn inside WSL")
    if not any(command["name"] == "node" and command["available"] for command in commands):
        steps.append("Install Node.js on the host if you want local helper tooling outside WSL")
    return steps
