from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from solana_agent.execution import CommandInterrupted, CommandTimedOut, ExecutionResult

SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "ANCHOR_PROVIDER_URL",
        "ANCHOR_WALLET",
        "CARGO_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PNPM_HOME",
        "RUSTUP_HOME",
        "SOLANA_CONFIG_DIR",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
    }
)


class ProcessRunner:
    def __init__(self, *, max_output_bytes: int = 1_000_000) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be greater than zero")
        self.max_output_bytes = max_output_bytes

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        environment: Mapping[str, str] | None = None,
    ) -> ExecutionResult:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must contain non-empty strings")
        safe_environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENVIRONMENT_KEYS}
        if environment:
            for key, value in environment.items():
                if key.upper() not in SAFE_ENVIRONMENT_KEYS:
                    raise ValueError(f"environment variable is not allowlisted: {key}")
                safe_environment[key] = value
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(argv),
                cwd=str(cwd),
                env=safe_environment,
                shell=False,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimedOut(
                f"process exceeded {timeout_seconds} seconds",
                stdout=self._as_text(exc.stdout),
                stderr=self._as_text(exc.stderr),
            ) from exc
        except KeyboardInterrupt as exc:
            raise CommandInterrupted("process interrupted by operator") from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_truncated = self._limit(completed.stdout)
        stderr, stderr_truncated = self._limit(completed.stderr)
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            metadata={
                "argv": list(argv),
                "cwd": str(cwd.resolve()),
                "duration_ms": duration_ms,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "shell": False,
            },
        )

    def _limit(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= self.max_output_bytes:
            return value, False
        marker = b"\n[output truncated by Solana Agent]\n"
        limit = max(0, self.max_output_bytes - len(marker))
        return (encoded[:limit] + marker).decode("utf-8", errors="replace"), True

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
