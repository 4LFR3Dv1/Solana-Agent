# Skill: anchor-test

## Purpose

Run the test flow for an Anchor workspace and persist the resulting evidence.

## Preconditions

- workspace exists
- dependencies are installed

## Inputs

- workspace path
- optional program name

## Procedure

1. inspect the workspace
2. run the build and test flow
3. capture stdout, stderr, and exit code
4. persist a test artifact

## Required Outputs

- test status
- executed commands
- artifact path for logs

## Success Criteria

At least one test validates `initialize > increment`.
