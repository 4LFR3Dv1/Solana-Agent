from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from solana_agent.execution import ExecutionRequest, ExecutionResult

from .process import ProcessRunner

PUBLIC_KEY = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
CLUSTER_URLS = {
    "devnet": "https://api.devnet.solana.com",
    "localnet": "http://127.0.0.1:8899",
    "localhost": "http://127.0.0.1:8899",
}


class SolanaCliAdapter:
    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        executable: str = "solana",
        default_cluster: str = "devnet",
    ) -> None:
        if default_cluster not in CLUSTER_URLS:
            raise ValueError("default cluster must be devnet, localnet, or localhost")
        self.runner = runner or ProcessRunner()
        self.executable = executable
        self.default_cluster = default_cluster

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        handlers = {
            "airdrop": self._airdrop,
            "address": self._address,
            "balance": self._balance,
            "deploy": self._deploy,
            "verify_program": self._verify_program,
        }
        try:
            handler = handlers[request.operation]
        except KeyError as exc:
            raise ValueError(f"unsupported Solana CLI operation: {request.operation}") from exc
        argv = handler(request.arguments)
        result = self.runner.run(
            argv,
            cwd=Path(request.cwd).resolve(),
            timeout_seconds=request.timeout_seconds,
        )
        return ExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            metadata={**result.metadata, "adapter": "solana", "operation": request.operation},
        )

    def _airdrop(self, arguments: dict[str, Any]) -> list[str]:
        raw_amount = arguments.get("amount", "1")
        try:
            amount = Decimal(str(raw_amount))
        except InvalidOperation as exc:
            raise ValueError("airdrop amount must be numeric") from exc
        if amount <= 0 or amount > 10:
            raise ValueError("airdrop amount must be greater than zero and at most 10 SOL")
        argv = [self.executable, "airdrop", format(amount, "f")]
        wallet = arguments.get("wallet")
        if wallet is not None:
            argv.append(self._public_key(wallet, "wallet"))
        argv.extend(["--url", self._cluster_url(arguments), "--output", "json"])
        return argv

    def _address(self, arguments: dict[str, Any]) -> list[str]:
        return [self.executable, "address"]

    def _balance(self, arguments: dict[str, Any]) -> list[str]:
        argv = [self.executable, "balance"]
        wallet = arguments.get("wallet")
        if wallet is not None:
            argv.append(self._public_key(wallet, "wallet"))
        argv.extend(["--url", self._cluster_url(arguments), "--output", "json"])
        return argv

    def _deploy(self, arguments: dict[str, Any]) -> list[str]:
        program_path = arguments.get("program_path")
        if not isinstance(program_path, str) or not program_path.strip():
            raise ValueError("Solana deploy requires program_path")
        path = Path(program_path).resolve()
        if not path.is_file() or path.suffix != ".so":
            raise ValueError("Solana deploy program_path must be an existing .so file")
        return [
            self.executable,
            "program",
            "deploy",
            str(path),
            "--url",
            self._cluster_url(arguments),
            "--output",
            "json",
        ]

    def _verify_program(self, arguments: dict[str, Any]) -> list[str]:
        program_id = self._public_key(arguments.get("program_id"), "program_id")
        return [
            self.executable,
            "program",
            "show",
            program_id,
            "--url",
            self._cluster_url(arguments),
            "--output",
            "json",
        ]

    def _cluster_url(self, arguments: dict[str, Any]) -> str:
        cluster = arguments.get("cluster", self.default_cluster)
        if not isinstance(cluster, str) or cluster not in CLUSTER_URLS:
            raise ValueError("cluster must be devnet, localnet, or localhost")
        return CLUSTER_URLS[cluster]

    @staticmethod
    def _public_key(value: Any, name: str) -> str:
        if not isinstance(value, str) or not PUBLIC_KEY.fullmatch(value):
            raise ValueError(f"{name} must be a base58 Solana public key")
        return value
