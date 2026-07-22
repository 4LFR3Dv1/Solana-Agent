from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from solana_agent.adapters import LocalValidator, SolanaRpcAdapter
from solana_agent.execution import ExecutionRequest


@pytest.mark.integration
def test_pinned_local_validator_answers_real_rpc(tmp_path: Path) -> None:
    if os.environ.get("SOLANA_AGENT_RUN_INTEGRATION") != "1":
        pytest.skip("set SOLANA_AGENT_RUN_INTEGRATION=1 inside the pinned toolchain container")
    if shutil.which("solana-test-validator") is None:
        pytest.skip("solana-test-validator is not installed")

    with LocalValidator(tmp_path / "ledger") as validator:
        adapter = SolanaRpcAdapter(validator.endpoint)
        request = ExecutionRequest("integration", "solana_rpc", "get_version", {}, str(tmp_path), 10)
        result = adapter.execute(request)

    assert result.exit_code == 0
    assert isinstance(result.metadata["result"], dict)
