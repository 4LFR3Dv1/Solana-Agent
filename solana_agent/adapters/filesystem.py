from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from solana_agent.execution import ExecutionRequest, ExecutionResult


class FilesystemAdapter:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        handlers = {
            "read": self._read,
            "create_workspace": self._create_workspace,
            "overwrite": self._overwrite,
        }
        try:
            handler = handlers[request.operation]
        except KeyError as exc:
            raise ValueError(f"unsupported filesystem operation: {request.operation}") from exc
        return handler(request.arguments)

    def _read(self, arguments: dict[str, Any]) -> ExecutionResult:
        path = self._path(arguments, "path")
        if not path.exists():
            return ExecutionResult(exit_code=1, stderr=f"path does not exist: {path}")
        metadata: dict[str, Any] = {
            "path": str(path),
            "exists": True,
            "is_file": path.is_file(),
            "is_directory": path.is_dir(),
        }
        if path.is_file():
            content = path.read_bytes()
            metadata.update(
                {
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        return ExecutionResult(exit_code=0, stdout=str(path), metadata=metadata)

    def _create_workspace(self, arguments: dict[str, Any]) -> ExecutionResult:
        destination = self._path(arguments, "destination")
        try:
            destination.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return ExecutionResult(exit_code=1, stderr=f"workspace already exists: {destination}")
        return ExecutionResult(
            exit_code=0,
            stdout=str(destination),
            metadata={"path": str(destination), "created": True},
        )

    def _overwrite(self, arguments: dict[str, Any]) -> ExecutionResult:
        path = self._path(arguments, "path")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("filesystem overwrite requires string content")
        if not path.parent.is_dir():
            raise ValueError(f"parent directory does not exist: {path.parent}")
        encoded = content.encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return ExecutionResult(
            exit_code=0,
            stdout=str(path),
            metadata={
                "path": str(path),
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            },
        )

    def _path(self, arguments: dict[str, Any], name: str) -> Path:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"filesystem operation requires {name}")
        candidate = Path(value)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.workspace_root / candidate).resolve()
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            raise ValueError(f"path escapes workspace root: {resolved}")
        return resolved
