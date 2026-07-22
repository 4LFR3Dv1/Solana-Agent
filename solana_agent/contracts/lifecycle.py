from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    mission_id: str
    status: RunStatus
    metadata: dict[str, Any]
    error: dict[str, Any] | None
    created_at: str
    updated_at: str
