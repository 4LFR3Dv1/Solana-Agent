# Architecture

Solana Agent is evolving toward six small runtime layers:

1. executable contracts
2. mission engine
3. command authority
4. transactional storage
5. execution adapters
6. evidence verification

## Agent Contract

Defined by:

- `agent.md`
- `prompts/`
- `missions/`
- `skills/`

These files define the agent identity, allowed behavior, supported mission flows, and success criteria.

Coding agents propose work. They are not the authority that validates or
executes governed commands.

## Executable Contracts

Defined by:

- `contracts/`
- `solana_agent/contracts/`

JSON schemas define portable representations. Python contracts define strict
runtime states and transitions.

## Declarative Mission Engine

Defined by:

- `missions/mission-pack.json`
- `missions/*.yaml`
- `solana_agent/missions/`

The loader validates YAML/JSON mission definitions and compiles their step DAG.
The engine persists per-step state, evaluates preconditions and acceptance,
pauses for bound approvals, and resumes without repeating succeeded work. See
`docs/declarative-missions.md`.

## Command Authority and Journal

Defined by:

- `solana_agent/authority/`
- `solana_agent/execution/`
- `solana_agent/storage/`

Command intent is persisted in SQLite before validation or execution. Commands,
events, and artifacts use separate ledgers. Rejected, failed, interrupted, and
timed-out operations remain queryable.

The authority layer applies versioned, fail-closed policy profiles and path,
cluster, wallet, spend, and secret guards. Policy decisions and bound approvals
are persisted separately from command state.

See `docs/command-journal.md` and `docs/policy-engine.md` for the current
contracts.

## Execution Adapters

Defined by:

- `scripts/solana/`
- `solana_agent/`

The shell adapters are small and deterministic. The Python runtime orchestrates missions, approvals, persistence, template rendering, and command execution.

The existing Solana runner is still legacy and is not connected to the new
governed journal yet. Future adapters must use structured arguments and
`shell=False`.

## Evidence and State Contracts

Defined by:

- `contracts/`
- `examples/`
- `.solana-agent/`

The repository stores schemas and examples. Live mission state stays local in `.solana-agent/`.

SQLite at `.solana-agent/runtime.db` is the new canonical local ledger. JSON is
used for portable contracts, exports, and evidence manifests.
