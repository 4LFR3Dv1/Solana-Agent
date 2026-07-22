# Policy Engine and Bound Approvals

## Purpose

The authority layer ensures that a coding agent can propose an operation but
cannot authorize it. Every material command must receive an explicit policy
decision before the executor is called. Missing policy is a denial.

## Decision pipeline

```text
command.planned
  -> command.validating
  -> policy.evaluated
       -> deny -> command.rejected
       -> allow -> command.authorized -> command.running
       -> require_approval
            -> approval.requested
            -> command.approval_required
            -> approval.approved | approval.denied | approval.expired
            -> approval.consumed
            -> command.authorized -> command.running
```

The policy decision, approval transition, and command transition are separate
append-only events. This lets an auditor distinguish what the policy required,
what the operator decided, and what the runtime executed.

## Profiles

The built-in policy version is `solana-agent-policy/1.0.0`.

| Profile | Intended authority |
| --- | --- |
| `read-only` | Environment inspection and workspace reads only |
| `local-safe` | Read-only plus local workspace, build, test, validator, and simulation operations |
| `devnet-safe` | Local-safe plus approved devnet airdrop, signing, deployment, and mutable invocation |

Rules are allowlisted by structured `adapter` and `operation` identifiers.
Unknown combinations use `default-deny`. Program upgrades, upgrade-authority
changes, and all mainnet operations are denied in the MVP.

## Guards

Guards run before the allowlist rule:

- Path guard resolves the working directory and path-like arguments and denies
  workspace escapes.
- Cluster guard denies `mainnet` and `mainnet-beta` and requires an explicit
  cluster for material Solana operations.
- Wallet guard accepts only a 32-byte base58 public key. Invalid wallet inputs
  are redacted before the policy snapshot is persisted.
- Spend guard requires a maximum lamport amount for material operations and
  denies values above the profile limit.
- Secret guard denies commands whose inputs required redaction.

Creating a workspace at an existing destination requires approval. This keeps
the safe default for new paths while preventing silent overwrite.

## Auditable decisions

Each decision records the policy version, matched `rule_id`, effect, reason,
risk level, required evidence types, redacted input snapshot, and SHA-256 hash
of that canonical snapshot. The complete versioned policy snapshot and its
hash are attached to the run.

## Bound approvals

An approval manifest binds the run, command, policy decision, policy version,
rule, exact policy input hash, and expiration time. The manifest has its own
SHA-256 hash.

An approval moves from `pending` to `approved` or `denied`. An approved record
must be consumed before execution and cannot be reused. Before consumption,
the runtime verifies expiration, command and run binding, decision snapshot
integrity, current command fields, and manifest integrity. A modified command
cannot reuse the original approval.

## Redaction boundary

Sensitive command arguments are redacted before the initial command record and
idempotency key are created. Policy snapshots, executor stdout/stderr, result
metadata, and exception messages also pass through redaction before persistence.

## Current integration boundary

The authority core is independent of the Solana toolchain and uses structured
adapter and operation names. The legacy hardcoded mission runner is not yet
connected. Future Solana adapters must remain unable to call the executor
without passing through this authority and journal pipeline.
