from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from solana_agent.contracts.authority import PolicyContext, PolicyDecision, PolicyEffect, RiskLevel
from solana_agent.contracts.command import CommandRecord

from .redaction import REDACTED


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class PolicyProfile(StrEnum):
    READ_ONLY = "read-only"
    LOCAL_SAFE = "local-safe"
    DEVNET_SAFE = "devnet-safe"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    id: str
    profiles: tuple[PolicyProfile, ...]
    adapter: str
    operation: str
    effect: PolicyEffect
    risk: RiskLevel
    reason: str
    required_evidence: tuple[str, ...] = ()

    def matches(self, profile: PolicyProfile, command: CommandRecord) -> bool:
        return (
            profile in self.profiles
            and self.adapter in {"*", command.adapter}
            and self.operation in {"*", command.operation}
        )


POLICY_VERSION = "solana-agent-policy/1.2.0"
SOLANA_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MATERIAL_SOLANA_OPERATIONS = frozenset({"airdrop", "sign", "deploy", "invoke"})
MATERIAL_ADAPTER_OPERATIONS = frozenset(
    {("solana", operation) for operation in MATERIAL_SOLANA_OPERATIONS} | {("anchor", "deploy")}
)
SAFE_CLUSTERS = frozenset(
    {
        "devnet",
        "localnet",
        "localhost",
        "http://127.0.0.1:8899",
        "http://localhost:8899",
        "https://api.devnet.solana.com",
    }
)

DEFAULT_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        "inspect-environment",
        tuple(PolicyProfile),
        "doctor",
        "inspect",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "environment inspection is read-only",
    ),
    PolicyRule(
        "read-workspace",
        tuple(PolicyProfile),
        "filesystem",
        "read",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "workspace reads are allowed inside the configured root",
    ),
    PolicyRule(
        "create-workspace",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "filesystem",
        "create_workspace",
        PolicyEffect.ALLOW,
        RiskLevel.MEDIUM,
        "new workspace creation is allowed when the destination does not exist",
        ("path.created",),
    ),
    PolicyRule(
        "overwrite-workspace",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "filesystem",
        "overwrite",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "overwriting workspace content requires operator approval",
        ("path.hash.before", "path.hash.after"),
    ),
    PolicyRule(
        "anchor-build",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "anchor",
        "build",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "local Anchor builds are allowed",
        ("build.result",),
    ),
    PolicyRule(
        "anchor-scaffold",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "anchor",
        "scaffold",
        PolicyEffect.ALLOW,
        RiskLevel.MEDIUM,
        "Anchor scaffolding is allowed inside the governed workspace",
        ("workspace.created",),
    ),
    PolicyRule(
        "anchor-test",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "anchor",
        "test",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "local Anchor tests are allowed",
        ("test.result",),
    ),
    PolicyRule(
        "package-install",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "package",
        "install",
        PolicyEffect.ALLOW,
        RiskLevel.MEDIUM,
        "locked package installation is allowed inside the governed workspace",
        ("dependencies.installed",),
    ),
    PolicyRule(
        "package-run",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "package",
        "run",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "declared package scripts can execute project code and require operator approval",
        ("script.result",),
    ),
    PolicyRule(
        "rpc-read",
        tuple(PolicyProfile),
        "solana_rpc",
        "*",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "allowlisted Solana JSON-RPC reads are non-material",
        ("rpc.response",),
    ),
    PolicyRule(
        "local-validator",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "solana",
        "start_validator",
        PolicyEffect.ALLOW,
        RiskLevel.MEDIUM,
        "starting a local validator is allowed",
        ("validator.health",),
    ),
    PolicyRule(
        "simulate-transaction",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "solana",
        "simulate",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "transaction simulation does not submit state changes",
        ("simulation.result",),
    ),
    PolicyRule(
        "verify-program",
        tuple(PolicyProfile),
        "solana",
        "verify_program",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "program verification is a read-only RPC operation",
        ("program.executable",),
    ),
    PolicyRule(
        "assemble-evidence",
        (PolicyProfile.LOCAL_SAFE, PolicyProfile.DEVNET_SAFE),
        "evidence",
        "assemble",
        PolicyEffect.ALLOW,
        RiskLevel.LOW,
        "local evidence assembly is allowed",
        ("evidence.manifest",),
    ),
    PolicyRule(
        "devnet-airdrop",
        (PolicyProfile.DEVNET_SAFE,),
        "solana",
        "airdrop",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.MEDIUM,
        "devnet airdrops require explicit approval",
        ("transaction.signature", "balance.after"),
    ),
    PolicyRule(
        "devnet-sign",
        (PolicyProfile.DEVNET_SAFE,),
        "solana",
        "sign",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "transaction signing requires explicit approval",
        ("transaction.message_hash",),
    ),
    PolicyRule(
        "devnet-deploy",
        (PolicyProfile.DEVNET_SAFE,),
        "solana",
        "deploy",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "devnet deployment requires explicit approval",
        ("program.executable", "transaction.signature"),
    ),
    PolicyRule(
        "anchor-devnet-deploy",
        (PolicyProfile.DEVNET_SAFE,),
        "anchor",
        "deploy",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "Anchor devnet deployment requires explicit approval",
        ("program.executable", "transaction.signature"),
    ),
    PolicyRule(
        "anchor-local-deploy",
        (PolicyProfile.LOCAL_SAFE,),
        "anchor",
        "deploy",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "Anchor local deployment remains an auditable material action",
        ("program.executable", "transaction.signature"),
    ),
    PolicyRule(
        "devnet-invoke",
        (PolicyProfile.DEVNET_SAFE,),
        "solana",
        "invoke",
        PolicyEffect.REQUIRE_APPROVAL,
        RiskLevel.HIGH,
        "mutable devnet invocation requires explicit approval",
        ("transaction.signature", "account.state.after"),
    ),
    PolicyRule(
        "deny-upgrade",
        tuple(PolicyProfile),
        "solana",
        "upgrade",
        PolicyEffect.DENY,
        RiskLevel.CRITICAL,
        "program upgrades are disabled in the MVP",
    ),
    PolicyRule(
        "deny-upgrade-authority",
        tuple(PolicyProfile),
        "solana",
        "set_upgrade_authority",
        PolicyEffect.DENY,
        RiskLevel.CRITICAL,
        "upgrade-authority changes are disabled in the MVP",
    ),
)


class PolicyEngine:
    def __init__(
        self,
        profile: PolicyProfile = PolicyProfile.READ_ONLY,
        *,
        rules: tuple[PolicyRule, ...] = DEFAULT_RULES,
        version: str = POLICY_VERSION,
        max_lamports: int = 2_000_000_000,
    ) -> None:
        self.profile = profile
        self.rules = rules
        self.version = version
        if max_lamports < 0:
            raise ValueError("max_lamports must not be negative")
        self.max_lamports = max_lamports

    def snapshot(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "profile": self.profile.value,
            "max_lamports": self.max_lamports,
            "rules": [
                {
                    "id": rule.id,
                    "profiles": [profile.value for profile in rule.profiles],
                    "adapter": rule.adapter,
                    "operation": rule.operation,
                    "effect": rule.effect.value,
                    "risk": rule.risk.value,
                    "reason": rule.reason,
                    "required_evidence": list(rule.required_evidence),
                }
                for rule in self.rules
            ],
        }
        return {**payload, "hash": sha256_json(payload)}

    def evaluate(self, command: CommandRecord, context: PolicyContext) -> PolicyDecision:
        snapshot = self.input_snapshot(command, context)
        guard = self._guard_decision(command, context, snapshot)
        if guard is not None:
            return guard
        for rule in self.rules:
            if rule.matches(self.profile, command):
                return self._decision(rule, snapshot)
        return self._deny(
            "default-deny",
            "operation is not allowlisted by the active policy profile",
            RiskLevel.HIGH,
            snapshot,
        )

    def input_snapshot(self, command: CommandRecord, context: PolicyContext) -> dict[str, Any]:
        return {
            "command": {
                "id": command.id,
                "run_id": command.run_id,
                "adapter": command.adapter,
                "operation": command.operation,
                "arguments": command.arguments,
                "cwd": command.cwd,
                "timeout_seconds": command.timeout_seconds,
                "expected_state": command.expected_state,
            },
            "context": {
                "workspace_root": str(Path(context.workspace_root).resolve()),
                "cluster": context.cluster,
                "wallet": context.wallet if self._valid_public_key(context.wallet) else REDACTED,
                "program_id": context.program_id,
                "max_lamports": context.max_lamports,
                "runtime_contract_hash": context.runtime_contract_hash,
            },
            "profile": self.profile.value,
            "policy_version": self.version,
        }

    def _guard_decision(
        self, command: CommandRecord, context: PolicyContext, snapshot: dict[str, Any]
    ) -> PolicyDecision | None:
        if self._contains_redaction(command.arguments):
            return self._deny("secret-input", "secret material was detected and redacted", RiskLevel.CRITICAL, snapshot)
        if context.cluster and context.cluster.lower() in {"mainnet", "mainnet-beta"}:
            return self._deny(
                "cluster-mainnet", "mainnet operations are disabled in the MVP", RiskLevel.CRITICAL, snapshot
            )
        if context.cluster and context.cluster.lower() not in SAFE_CLUSTERS:
            return self._deny(
                "cluster-not-allowlisted",
                "cluster endpoint is not allowlisted by the active policy",
                RiskLevel.CRITICAL,
                snapshot,
            )
        is_material_solana = (command.adapter, command.operation) in MATERIAL_ADAPTER_OPERATIONS
        if is_material_solana and not context.cluster:
            return self._deny(
                "cluster-unspecified",
                "material Solana operations require an explicit cluster",
                RiskLevel.HIGH,
                snapshot,
            )
        if is_material_solana and not self._valid_public_key(context.wallet):
            return self._deny(
                "wallet-invalid", "a valid public Solana wallet is required", RiskLevel.CRITICAL, snapshot
            )
        if is_material_solana and context.max_lamports is None:
            return self._deny(
                "spend-unspecified", "material Solana operations require a lamport limit", RiskLevel.HIGH, snapshot
            )
        if is_material_solana and not self._valid_hash(context.runtime_contract_hash):
            return self._deny(
                "runtime-contract-invalid",
                "material Solana operations require a valid runtime contract hash",
                RiskLevel.HIGH,
                snapshot,
            )
        if context.max_lamports is not None and (context.max_lamports < 0 or context.max_lamports > self.max_lamports):
            return self._deny(
                "spend-limit", "requested lamports exceed the active policy limit", RiskLevel.CRITICAL, snapshot
            )
        root = Path(context.workspace_root).resolve()
        cwd = Path(command.cwd).resolve()
        if cwd != root and root not in cwd.parents:
            return self._deny(
                "path-escape", "command cwd escapes the configured workspace", RiskLevel.CRITICAL, snapshot
            )
        for key in ("path", "destination", "workspace", "program_path"):
            value = command.arguments.get(key)
            if isinstance(value, str):
                target = Path(value)
                resolved = (root / target).resolve() if not target.is_absolute() else target.resolve()
                if resolved != root and root not in resolved.parents:
                    return self._deny(
                        "path-escape", f"argument {key} escapes the configured workspace", RiskLevel.CRITICAL, snapshot
                    )
                if command.adapter == "filesystem" and command.operation == "create_workspace" and resolved.exists():
                    return PolicyDecision(
                        PolicyEffect.REQUIRE_APPROVAL,
                        "workspace-exists",
                        self.version,
                        "creating over an existing workspace requires approval",
                        RiskLevel.HIGH,
                        ("path.hash.before", "path.hash.after"),
                        snapshot,
                        sha256_json(snapshot),
                    )
        return None

    @staticmethod
    def _valid_public_key(value: str | None) -> bool:
        if not value or any(character not in SOLANA_ALPHABET for character in value):
            return False
        number = 0
        for character in value:
            number = number * 58 + SOLANA_ALPHABET.index(character)
        decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
        decoded = (b"\x00" * (len(value) - len(value.lstrip("1")))) + decoded
        return len(decoded) == 32

    @staticmethod
    def _valid_hash(value: str | None) -> bool:
        return bool(value and len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value))

    @staticmethod
    def _contains_redaction(value: Any) -> bool:
        if value == REDACTED:
            return True
        if isinstance(value, dict):
            return any(PolicyEngine._contains_redaction(item) for item in value.values())
        if isinstance(value, list):
            return any(PolicyEngine._contains_redaction(item) for item in value)
        return False

    def _decision(self, rule: PolicyRule, snapshot: dict[str, Any]) -> PolicyDecision:
        return PolicyDecision(
            rule.effect,
            rule.id,
            self.version,
            rule.reason,
            rule.risk,
            rule.required_evidence,
            snapshot,
            sha256_json(snapshot),
        )

    def _deny(self, rule_id: str, reason: str, risk: RiskLevel, snapshot: dict[str, Any]) -> PolicyDecision:
        return PolicyDecision(
            PolicyEffect.DENY, rule_id, self.version, reason, risk, (), snapshot, sha256_json(snapshot)
        )
