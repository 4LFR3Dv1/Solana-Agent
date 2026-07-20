from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    command_id: str
    adapter: str
    operation: str
    arguments: dict[str, Any]
    cwd: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Executor(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


class CommandTimedOut(TimeoutError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CommandInterrupted(RuntimeError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class FakeExecutor:
    """Deterministic executor used to test the runtime without external tools."""

    def __init__(
        self,
        result: ExecutionResult | None = None,
        *,
        error: Exception | None = None,
        callback: Callable[[ExecutionRequest], ExecutionResult] | None = None,
    ) -> None:
        self.result = result or ExecutionResult(exit_code=0)
        self.error = error
        self.callback = callback
        self.requests: list[ExecutionRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.callback is not None:
            return self.callback(request)
        return self.result
