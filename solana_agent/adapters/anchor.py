from __future__ import annotations

from pathlib import Path
from typing import Any

from solana_agent.execution import ExecutionRequest, ExecutionResult

from .process import ProcessRunner


class AnchorAdapter:
    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        executable: str = "anchor",
        package_manager: str = "pnpm",
        default_cluster: str = "localnet",
    ) -> None:
        if default_cluster not in {"devnet", "localnet", "localhost"}:
            raise ValueError("default cluster must be devnet, localnet, or localhost")
        self.runner = runner or ProcessRunner()
        self.executable = executable
        self.package_manager = package_manager
        self.default_cluster = "localnet" if default_cluster == "localhost" else default_cluster

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        handlers = {
            "scaffold": self._scaffold,
            "build": self._build,
            "test": self._test,
            "deploy": self._deploy,
            "keys_list": self._keys_list,
        }
        try:
            handler = handlers[request.operation]
        except KeyError as exc:
            raise ValueError(f"unsupported Anchor operation: {request.operation}") from exc
        argv, cwd = handler(request)
        result = self.runner.run(argv, cwd=cwd, timeout_seconds=request.timeout_seconds)
        return self._with_adapter_metadata(result, request.operation)

    def _scaffold(self, request: ExecutionRequest) -> tuple[list[str], Path]:
        workspace = self._required_path(request.arguments, "workspace")
        project_name = self._required_string(request.arguments, "project_name")
        if workspace.exists():
            raise ValueError(f"Anchor scaffold destination already exists: {workspace}")
        if workspace.name != project_name:
            raise ValueError("project_name must match the workspace directory name")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        return (
            [self.executable, "init", project_name, "--package-manager", self.package_manager],
            workspace.parent,
        )

    def _build(self, request: ExecutionRequest) -> tuple[list[str], Path]:
        argv = [self.executable, "build"]
        if request.arguments.get("verifiable") is True:
            argv.append("--verifiable")
        return argv, self._cwd(request)

    def _test(self, request: ExecutionRequest) -> tuple[list[str], Path]:
        argv = [self.executable, "test"]
        if request.arguments.get("skip_local_validator") is True:
            argv.append("--skip-local-validator")
        return argv, self._cwd(request)

    def _deploy(self, request: ExecutionRequest) -> tuple[list[str], Path]:
        argv = [self.executable, "deploy"]
        cluster = request.arguments.get("cluster", self.default_cluster)
        if not isinstance(cluster, str) or cluster not in {"devnet", "localnet", "localhost"}:
            raise ValueError("Anchor deploy cluster must be devnet, localnet, or localhost")
        normalized_cluster = "localnet" if cluster == "localhost" else cluster
        argv.extend(["--provider.cluster", normalized_cluster])
        return argv, self._cwd(request)

    def _keys_list(self, request: ExecutionRequest) -> tuple[list[str], Path]:
        return [self.executable, "keys", "list"], self._cwd(request)

    @staticmethod
    def _cwd(request: ExecutionRequest) -> Path:
        cwd = Path(request.cwd).resolve()
        if not cwd.is_dir():
            raise ValueError(f"Anchor workspace does not exist: {cwd}")
        return cwd

    @staticmethod
    def _required_string(arguments: dict[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Anchor operation requires {name}")
        return value

    @classmethod
    def _required_path(cls, arguments: dict[str, Any], name: str) -> Path:
        return Path(cls._required_string(arguments, name)).resolve()

    @staticmethod
    def _with_adapter_metadata(result: ExecutionResult, operation: str) -> ExecutionResult:
        return ExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            metadata={**result.metadata, "adapter": "anchor", "operation": operation},
        )
