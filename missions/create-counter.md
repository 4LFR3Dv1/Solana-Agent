# Mission: Create Counter

Canonical executable definition: `missions/create-counter.yaml`.

## Goal

Create a new Anchor counter program, test the `initialize > increment` flow, deploy it to devnet, invoke it, and generate an evidence pack.

## Skill Chain

1. `solana-bootstrap`
2. `wallet-safety`
3. `anchor-scaffold`
4. `anchor-test`
5. `anchor-deploy`
6. `solana-invoke`
7. `explorer-evidence`

## Required Inputs

- workspace path
- project name
- cluster, default `devnet`

## Required Outputs

- mission record
- test result artifact
- deployment record
- invocation artifacts
- evidence pack

The governed CLI and approval/resume procedure are documented in
[`docs/pr5-devnet-e2e.md`](../docs/pr5-devnet-e2e.md).
