"""Explicit test-only process kill points for gateway chaos verification."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

KILL_POINTS = {
    "after_execution_validated_before_claim",
    "after_signature_and_broadcast_intent_persisted",
    "before_send_transaction",
    "after_send_transaction_response_before_persist",
}


class ChaosHook(Protocol):
    def reach(self, point: str, context: Mapping[str, Any]) -> None: ...


class ProcessKillHook:
    """Terminates the current process only at one explicitly configured point."""

    def __init__(
        self,
        kill_point: str,
        *,
        sentinel: Path | None = None,
        exit_code: int = 86,
    ) -> None:
        if kill_point not in KILL_POINTS:
            raise ValueError(f"unsupported chaos kill point: {kill_point}")
        self.kill_point = kill_point
        self.sentinel = sentinel
        self.exit_code = exit_code

    def reach(self, point: str, context: Mapping[str, Any]) -> None:
        if point != self.kill_point:
            return
        if self.sentinel is not None:
            self.sentinel.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.sentinel.with_suffix(self.sentinel.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "type": "solana_agent_chaos_kill",
                        "point": point,
                        "context": dict(context),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.sentinel)
        os._exit(self.exit_code)


def reach(
    hook: ChaosHook | None,
    point: str,
    context: Mapping[str, Any],
) -> None:
    if point not in KILL_POINTS:
        raise ValueError(f"unknown chaos point: {point}")
    if hook is not None:
        hook.reach(point, context)
