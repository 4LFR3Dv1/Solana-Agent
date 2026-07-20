# Architecture

Solana Agent is organized into three layers:

1. Agent contract
2. Execution adapters
3. Evidence and state contracts

## Agent Contract

Defined by:

- `agent.md`
- `prompts/`
- `missions/`
- `skills/`

These files define the agent identity, allowed behavior, supported mission flows, and success criteria.

## Execution Adapters

Defined by:

- `scripts/solana/`
- `solana_agent/`

The shell adapters are small and deterministic. The Python runtime orchestrates missions, approvals, persistence, template rendering, and command execution.

## Evidence and State Contracts

Defined by:

- `contracts/`
- `examples/`
- `.solana-agent/`

The repository stores schemas and examples. Live mission state stays local in `.solana-agent/`.
