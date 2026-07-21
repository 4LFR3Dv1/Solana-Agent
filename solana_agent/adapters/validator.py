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
                _, stderr = self.process.communicate(timeout=1)
                raise LocalValidatorError(f"local validator exited during startup: {stderr.strip()}")
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
