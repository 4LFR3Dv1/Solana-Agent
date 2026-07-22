from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class PolicyContext:
    workspace_root: str
    cluster: str | None = None
    wallet: str | None = None
    program_id: str | None = None
    max_lamports: int | None = None
    runtime_contract_hash: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: PolicyEffect
    rule_id: str
    policy_version: str
    reason: str
    risk: RiskLevel
    required_evidence: tuple[str, ...]
    input_snapshot: dict[str, Any]
    input_hash: str


@dataclass(frozen=True, slots=True)
class PolicyDecisionRecord:
    id: str
    run_id: str
    command_id: str
    effect: PolicyEffect
    rule_id: str
    policy_version: str
    reason: str
    risk: RiskLevel
    required_evidence: tuple[str, ...]
    input_snapshot: dict[str, Any]
    input_hash: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    run_id: str
    command_id: str
    policy_decision_id: str
    manifest: dict[str, Any]
    manifest_hash: str
    status: ApprovalStatus
    requested_at: str
    expires_at: str
    decided_at: str | None
    approved_by: str | None
    note: str | None
    consumed_at: str | None
