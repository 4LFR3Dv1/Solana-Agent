from __future__ import annotations

import pytest

from solana_agent.execution import CommandSpec
from solana_agent.execution.idempotency import build_command_idempotency_key


def build_key(**overrides: object) -> str:
    values = {
        "run_id": "run-1",
        "step_id": "build",
        "adapter": "fake",
        "operation": "build",
        "arguments": {"release": False},
        "cwd": "/workspace",
        "timeout_seconds": 60,
        "expected_state": None,
    }
    values.update(overrides)
    return build_command_idempotency_key(**values)  # type: ignore[arg-type]


def test_idempotency_key_is_stable_for_equivalent_arguments() -> None:
    first = build_key(arguments={"cluster": "devnet", "program": "counter"})
    second = build_key(arguments={"program": "counter", "cluster": "devnet"})

    assert first == second
    assert len(first) == 64


def test_idempotency_key_changes_with_execution_scope() -> None:
    base = build_key()

    assert build_key(run_id="run-2") != base
    assert build_key(step_id="test") != base
    assert build_key(timeout_seconds=120) != base
    assert build_key(expected_state="workspace-clean") != base


def test_explicit_idempotency_key_must_be_a_sha256_digest() -> None:
    with pytest.raises(ValueError, match="64-character hexadecimal"):
        CommandSpec(
            run_id="run-1",
            step_id="build",
            adapter="fake",
            operation="build",
            idempotency_key="user-provided-short-key",
        )
