# Safety Model

The agent is intentionally conservative around Solana execution.

## Core Rules

- no seed phrase handling in outputs
- no implicit cluster assumptions during deploy or invoke
- no successful status without evidence
- no destructive file actions without approval
- no mainnet action by default

## Sensitive Operations

Sensitive operations must be explicit in the mission record and approval record.
