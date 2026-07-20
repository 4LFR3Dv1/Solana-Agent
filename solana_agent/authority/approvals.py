from __future__ import annotations

from datetime import UTC, datetime, timedelta

from solana_agent.contracts.authority import ApprovalRecord, ApprovalStatus, PolicyDecisionRecord, PolicyEffect
from solana_agent.contracts.command import CommandRecord
from solana_agent.storage.repositories import JournalRepository

from .policy import sha256_json
from .redaction import redact_text


class ApprovalError(ValueError):
    pass


class ApprovalService:
    def __init__(self, repository: JournalRepository, *, default_ttl_seconds: int = 900) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be greater than zero")
        self.repository = repository
        self.default_ttl_seconds = default_ttl_seconds

    def request(
        self,
        command: CommandRecord,
        decision: PolicyDecisionRecord,
        *,
        ttl_seconds: int | None = None,
    ) -> ApprovalRecord:
        if decision.effect != PolicyEffect.REQUIRE_APPROVAL:
            raise ValueError("approval requests require a require_approval policy decision")
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        expires_at = self._iso(datetime.now(UTC) + timedelta(seconds=ttl))
        manifest = {
            "command_id": command.id,
            "run_id": command.run_id,
            "policy_decision_id": decision.id,
            "policy_version": decision.policy_version,
            "rule_id": decision.rule_id,
            "input_hash": decision.input_hash,
            "expires_at": expires_at,
        }
        return self.repository.create_approval(
            command_id=command.id,
            policy_decision_id=decision.id,
            manifest=manifest,
            manifest_hash=sha256_json(manifest),
            expires_at=expires_at,
        )

    def approve(self, approval_id: str, *, approved_by: str, note: str | None = None) -> ApprovalRecord:
        if not approved_by.strip():
            raise ValueError("approved_by must not be empty")
        return self.repository.decide_approval(
            approval_id,
            status=ApprovalStatus.APPROVED,
            approved_by=approved_by,
            note=redact_text(note) if note else None,
        )

    def deny(self, approval_id: str, *, approved_by: str, note: str | None = None) -> ApprovalRecord:
        if not approved_by.strip():
            raise ValueError("approved_by must not be empty")
        return self.repository.decide_approval(
            approval_id,
            status=ApprovalStatus.DENIED,
            approved_by=approved_by,
            note=redact_text(note) if note else None,
        )

    def consume(self, approval_id: str, command: CommandRecord) -> ApprovalRecord:
        approval = self.repository.require_approval(approval_id)
        decision = self.repository.require_policy_decision(approval.policy_decision_id)
        now = datetime.now(UTC)
        if approval.status == ApprovalStatus.PENDING and self._parse(approval.expires_at) <= now:
            approval = self.repository.expire_approval(approval.id)
        if approval.status != ApprovalStatus.APPROVED:
            raise ApprovalError(f"approval is not usable: {approval.status.value}")
        if self._parse(approval.expires_at) <= now:
            self.repository.expire_approval(approval.id)
            raise ApprovalError("approval has expired")
        if approval.command_id != command.id or approval.run_id != command.run_id:
            raise ApprovalError("approval is bound to a different command")
        command_snapshot = decision.input_snapshot.get("command")
        current_snapshot = {
            "id": command.id,
            "run_id": command.run_id,
            "adapter": command.adapter,
            "operation": command.operation,
            "arguments": command.arguments,
            "cwd": command.cwd,
            "timeout_seconds": command.timeout_seconds,
            "expected_state": command.expected_state,
        }
        if command_snapshot != current_snapshot or sha256_json(decision.input_snapshot) != decision.input_hash:
            raise ApprovalError("command or policy inputs changed after approval was requested")
        if sha256_json(approval.manifest) != approval.manifest_hash:
            raise ApprovalError("approval manifest integrity check failed")
        return self.repository.consume_approval(approval.id)

    def expire_pending(self) -> list[ApprovalRecord]:
        return self.repository.expire_pending_approvals(self._iso(datetime.now(UTC)))

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
