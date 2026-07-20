# Skill: solana-invoke

## Purpose

Invoke instructions against a deployed program and capture evidence of successful interaction.

## Preconditions

- program was deployed
- wallet and cluster are confirmed

## Inputs

- workspace path
- program name
- instruction sequence, starting with `initialize` then `increment`

## Procedure

1. determine the invocation client or test harness
2. execute the target instructions
3. capture transaction signatures
4. persist invocation artifacts

## Required Outputs

- invocation status
- transaction signatures
- Explorer links
