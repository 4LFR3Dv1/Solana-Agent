from __future__ import annotations

import re
from pathlib import Path

from solana_agent.execution import ExecutionRequest, ExecutionResult

from .process import ProcessRunner

SCRIPT_NAME = re.compile(r"^[A-Za-z0-9:_-]+$")


class PackageManagerAdapter:
    def __init__(self, runner: ProcessRunner | None = None, *, executable: str = "pnpm") -> None:
        self.runner = runner or ProcessRunner()
        self.executable = executable

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        cwd = Path(request.cwd).resolve()
        if not cwd.is_dir():
            raise ValueError(f"package workspace does not exist: {cwd}")
        if request.operation == "install":
            argv = [self.executable, "install", "--ignore-scripts"]
            if (cwd / "pnpm-lock.yaml").is_file():
                argv.append("--frozen-lockfile")
        elif request.operation == "run":
            script = request.arguments.get("script")
            if not isinstance(script, str) or not SCRIPT_NAME.fullmatch(script):
                raise ValueError("package run requires a safe script name")
            argv = [self.executable, "run", script]
        else:
            raise ValueError(f"unsupported package-manager operation: {request.operation}")
        result = self.runner.run(argv, cwd=cwd, timeout_seconds=request.timeout_seconds)
        return ExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            metadata={**result.metadata, "adapter": "package", "operation": request.operation},
        )
