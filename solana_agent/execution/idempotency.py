from __future__ import annotations

import hashlib
import json
from typing import Any


def build_command_idempotency_key(
    *,
    run_id: str,
    step_id: str,
    adapter: str,
    operation: str,
    arguments: dict[str, Any],
    cwd: str,
    timeout_seconds: int,
    expected_state: str | None = None,
) -> str:
    payload = {
        "adapter": adapter,
        "arguments": arguments,
        "cwd": cwd,
        "expected_state": expected_state,
        "operation": operation,
        "run_id": run_id,
        "step_id": step_id,
        "timeout_seconds": timeout_seconds,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
