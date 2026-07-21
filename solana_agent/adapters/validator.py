from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import TracebackType


class LocalValidatorError(RuntimeError):
    pass


class LocalValidator:
    def __init__(
        self,
        ledger_path: Path,
        *,
        executable: str = "solana-test-validator",
        rpc_port: int = 8899,
        faucet_port: int = 9900,
        startup_timeout_seconds: int = 30,
    ) -> None:
        self.ledger_path = ledger_path.resolve()
        self.executable = executable
        self.rpc_port = rpc_port
        self.faucet_port = faucet_port
        self.startup_timeout_seconds = startup_timeout_seconds
        self.process: subprocess.Popen[str] | None = None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.rpc_port}"

    def start(self) -> LocalValidator:
        if self.process is not None:
            raise LocalValidatorError("local validator is already running")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [
                self.executable,
                "--ledger",
                str(self.ledger_path),
                "--rpc-port",
                str(self.rpc_port),
                "--faucet-port",
                str(self.faucet_port),
                "--reset",
                "--quiet",
            ],
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                return_code = self.process.returncode
                diagnostics = self._startup_diagnostics(stdout, stderr)
                raise LocalValidatorError(
                    f"local validator exited during startup with code {return_code}: {diagnostics}"
                )
            if self._healthy():
                return self
            time.sleep(0.2)
        self.stop()
        raise LocalValidatorError("local validator did not become healthy before the deadline")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    def _startup_diagnostics(self, stdout: str, stderr: str) -> str:
        details = []
        if stdout.strip():
            details.append(f"stdout={stdout.strip()}")
        if stderr.strip():
            details.append(f"stderr={stderr.strip()}")
        log_candidates = sorted(
            self.ledger_path.glob("validator*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if log_candidates:
            log_tail = log_candidates[0].read_text(encoding="utf-8", errors="replace")[-20_000:].strip()
            if log_tail:
                details.append(f"validator_log={log_tail}")
        return " | ".join(details) if details else "no process output or validator log was produced"

    def _healthy(self) -> bool:
        payload = json.dumps({"jsonrpc": "2.0", "id": "validator-health", "method": "getHealth", "params": []}).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            return False
        return isinstance(value, dict) and value.get("result") == "ok"

    def __enter__(self) -> LocalValidator:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.stop()
