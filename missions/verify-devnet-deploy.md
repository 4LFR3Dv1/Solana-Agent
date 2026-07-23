# Mission: Verify Devnet Deploy

Canonical executable definition: `missions/verify-devnet-deploy.yaml`.

## Goal

Inspect public deployment identifiers, confirm program and transaction state over
RPC, and assemble a machine-readable evidence bundle for a devnet deployment.

## Required Inputs

- Program ID
- counter account public key
- deploy transaction signature
- initialize transaction signature
- increment transaction signature
- expected counter value, default `1`

The verifier is independent of the original deployment run and does not require
the original workspace or its private signing material.

## Skill Chain

1. `solana-bootstrap`
2. `wallet-safety`
3. `explorer-evidence`
