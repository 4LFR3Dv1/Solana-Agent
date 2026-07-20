from __future__ import annotations

from solana_agent.runtime import RuntimeErrorWithContext, slug_to_camel, slug_to_snake


def test_slug_conversions_are_deterministic() -> None:
    assert slug_to_snake("counter-demo") == "counter_demo"
    assert slug_to_camel("counter-demo") == "counterDemo"
    assert slug_to_camel("counter_demo") == "counterDemo"


def test_runtime_error_preserves_structured_context() -> None:
    error = RuntimeErrorWithContext("cluster mismatch", expected="devnet", actual="mainnet-beta")

    assert error.to_dict() == {
        "ok": False,
        "error": "cluster mismatch",
        "context": {"expected": "devnet", "actual": "mainnet-beta"},
    }
