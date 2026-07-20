# Output Contracts Prompt

Every mission output should be structured around:

- `summary`
- `environment`
- `approvals`
- `commands`
- `artifacts`
- `deployment`
- `issues`
- `next_action`

When possible, script adapters should emit machine-readable JSON to stdout and human-readable logs to stderr.
