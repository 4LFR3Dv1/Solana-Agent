from __future__ import annotations

from pathlib import Path

from solana_agent.adapters import LocalValidator


def test_validator_startup_diagnostics_include_streams_and_bounded_log(tmp_path: Path) -> None:
    validator = LocalValidator(tmp_path / "ledger")
    validator.ledger_path.mkdir()
    (validator.ledger_path / "validator.log").write_text("validator panic detail\n", encoding="utf-8")

    diagnostics = validator._startup_diagnostics("status output", "native error")

    assert "stdout=status output" in diagnostics
    assert "stderr=native error" in diagnostics
    assert "validator_log=validator panic detail" in diagnostics
