# Governed Command Journal

Status: `implemented-foundation`
Version: `1`
Date: `2026-07-20`

## Objective

The command journal guarantees that no command intent, validation decision,
failure, or execution result disappears from the runtime history.

The implementation is deliberately independent of Solana CLI, Anchor, WSL,
wallets, and network access.

## Canonical flow

```text
planned
→ validating
→ rejected | approval_required | authorized
→ running
→ succeeded | failed | timed_out | interrupted
```

Cancellation is allowed only from explicit pre-execution states. A command in
`running` is interrupted rather than cancelled so the ledger reflects that
execution had already begun.

Terminal commands cannot transition back to an executable state.

## Journal-before-execute invariant

The order is mandatory:

1. persist the command as `planned`;
2. append `command.planned`;
3. validate the command;
4. persist the validation decision;
5. persist `running` before calling the executor;
6. execute through an adapter;
7. store stdout and stderr as separate hashed artifacts;
8. persist a terminal result;
9. append the corresponding terminal event.

Rejected commands remain in the journal and never reach the executor.

## Ledgers

### Runs

Own the execution scope and mission correlation.

### Commands

Store intent, structured arguments, lifecycle, policy decision, timestamps,
result, error, and references to stream artifacts.

### Events

Store append-only facts with a monotonically increasing sequence scoped to a
run.

### Artifacts

Store stdout and stderr independently with SHA-256 hashes and byte sizes.

The first implementation stores stream content in SQLite. A later evidence
layer may externalize large content by hash without changing command semantics.

## Idempotency

The default idempotency key is the SHA-256 hash of canonical JSON containing:

- run ID;
- step ID;
- adapter;
- operation;
- structured arguments;
- cwd;
- timeout;
- expected state.

The database enforces uniqueness for `(run_id, idempotency_key)`. A replay
returns the existing command and does not invoke the executor again.

## Failure semantics

The journal distinguishes:

- `failed`: non-zero exit or unexpected executor exception;
- `timed_out`: adapter exceeded its deadline;
- `interrupted`: operator interruption or orphan recovery;
- `rejected`: validation or policy denied execution.

Errors are structured with `code`, `message`, and optional exception `type`.
Partial stdout and stderr from timeouts or interruptions are preserved.

## Recovery

At runtime startup, a command found in `running` without a live process is
marked `interrupted` with error code `orphaned_command`. It is never silently
retried.

## Current boundary

The journal currently uses a deterministic fake executor for validation. It is
not connected to the hardcoded Solana mission runner. That integration will
only occur after policy, approvals, redaction, and allowlisted adapters are in
place.
