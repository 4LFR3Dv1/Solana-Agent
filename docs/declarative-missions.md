# Declarative Mission Engine

## Purpose

The mission engine turns a versioned mission definition into a governed DAG of
commands. Mission content describes what to execute; registered adapters,
Policy Engine, bound approvals, and the command journal retain execution
authority.

The core engine is independent of Solana CLI and Anchor. PR4 will provide the
real adapter implementations. Tests use deterministic fake executors.

## Mission pack

`missions/mission-pack.json` is the root manifest. It declares the pack ID,
version, and YAML or JSON mission files. The loader normalizes every mission
and calculates:

- a SHA-256 hash for each `MissionDefinition`;
- a SHA-256 hash for the complete ordered mission pack.

The core pack contains:

- `create-counter`;
- `deploy-existing-program`;
- `verify-devnet-deploy`.

Adding another mission requires a YAML or JSON definition and a manifest entry.
It does not require a branch in the Python runner.

## Step DAG

Each step declares:

- stable step ID;
- adapter and structured operation;
- dependency IDs;
- structured arguments and working directory templates;
- timeout;
- executable preconditions;
- executable acceptance criteria.

The loader rejects duplicate IDs, missing dependencies, self-dependencies,
cycles, empty operations, and invalid timeouts before a run is created.

Templates use strict paths such as `{{inputs.workspace}}`,
`{{runtime.cluster}}`, and `{{steps.build.status}}`. Missing template values are
errors rather than empty strings.

## Preconditions and acceptance

Initial preconditions:

- `input_present`;
- `path_exists`;
- `path_not_exists`;
- `equals`.

Initial acceptance checks:

- `command_succeeded`;
- `exit_code_equals`;
- `result_equals`;
- `path_exists`;
- `artifact_kind_exists`.

A successful process exit does not complete a step when its acceptance checks
fail.

## Step lifecycle

```text
pending -> running -> succeeded
                   -> failed
                   -> waiting_approval -> succeeded | failed

pending -> blocked
failed | blocked -> running  # explicit resume attempt
```

Succeeded steps are terminal and never execute again during resume. A failed
dependency marks downstream steps as `blocked`. When a failed dependency later
succeeds on resume, blocked dependents become eligible for their first attempt.

Every attempt receives a distinct idempotency key derived from the run, step,
step-definition hash, and attempt number.

## Pause and resume

When policy returns `require_approval`, the step is persisted as
`waiting_approval` and the run returns control without calling the executor.
Resume keeps waiting while the approval is pending. An approved manifest is
validated and consumed by the PR2 authority layer before execution.

Resume fails closed if any of these differ from the original run:

- mission definition hash;
- mission pack hash;
- Runtime Contract hash;
- redacted mission-input hash.

This prevents a completed step or existing approval from being reused under a
different mission or environment.

## Run binding

Run metadata stores:

- mission ID, version, and definition hash;
- mission-pack ID, version, and hash;
- Runtime Contract snapshot and hash;
- redacted inputs and their hash;
- Policy Engine snapshot and hash.

The `mission_steps` SQLite ledger stores status, definition hash, attempt,
command reference, result, and error separately for every step.

## CLI inspection

The pack can be inspected without Solana installed:

```bash
solana-agent missions list
solana-agent missions show create-counter
solana-agent missions validate
```

The older hardcoded runner remains a disconnected compatibility path until PR4
provides governed production adapters. New runtime development must target
`MissionEngine`; no new mission-specific Python runner methods should be added.
