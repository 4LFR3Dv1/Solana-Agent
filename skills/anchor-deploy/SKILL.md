# Skill: anchor-deploy

## Purpose

Deploy an Anchor program to devnet and persist a verifiable deployment record.

## Preconditions

- tests have passed or the failure was explicitly accepted
- cluster is confirmed
- wallet is confirmed
- user approval for deploy has been recorded

## Inputs

- workspace path
- program name
- cluster, expected `devnet`

## Procedure

1. validate cluster and wallet context
2. run the build if needed
3. run the deploy command
4. capture Program ID and deploy signature
5. persist a deployment record

## Required Outputs

- Program ID
- deploy signature
- Explorer link
- deployment record path
