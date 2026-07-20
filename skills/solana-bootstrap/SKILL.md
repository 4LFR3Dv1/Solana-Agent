# Skill: solana-bootstrap

## Purpose

Inspect the local Solana and Anchor environment and produce a machine-readable environment report.

## Preconditions

- a local shell runtime is available

## Inputs

- optional cluster override

## Procedure

1. check for `rustc`, `solana`, `anchor`, `node`, and `yarn`
2. inspect `solana config get`
3. inspect the active wallet address
4. emit a structured environment report

## Required Outputs

- tool availability
- tool versions when available
- active cluster
- active wallet address when available
- readiness status

## Failure Modes

- missing Solana CLI
- missing Anchor CLI
- no wallet configured
- malformed Solana config
