# Skill: anchor-scaffold

## Purpose

Create or prepare an Anchor workspace for a supported template, starting with the counter program.

## Preconditions

- environment checks passed
- target path is writable

## Inputs

- workspace path
- project name
- template name, default `anchor-counter`

## Procedure

1. inspect target path
2. require approval before overwriting an existing workspace
3. create a new Anchor workspace or adapt an empty directory
4. apply the selected template
5. return workspace metadata

## Required Outputs

- workspace path
- program name
- applied template
- files created or updated
