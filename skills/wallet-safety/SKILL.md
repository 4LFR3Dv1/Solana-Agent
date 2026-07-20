# Skill: wallet-safety

## Purpose

Validate wallet and cluster context before any signing or deploy action.

## Preconditions

- `solana-bootstrap` has already run

## Inputs

- expected cluster
- optional expected wallet alias or address

## Procedure

1. read the active Solana config
2. read the active wallet address
3. compare actual cluster against expected cluster
4. require approval for sensitive actions if context changes are needed

## Required Outputs

- cluster confirmation
- wallet confirmation
- approval requirement summary

## Safety Rules

- never reveal seed phrases
- never infer wallet identity from file names alone
