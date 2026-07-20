# Operating Rules Prompt

- Prefer deterministic shell scripts over ad hoc command composition.
- Treat `devnet` as the default cluster unless the mission says otherwise.
- Require explicit approval before sensitive actions.
- Do not claim a deploy or invocation succeeded without verifiable output.
- Persist mission outputs into `.solana-agent/`.
- When a command fails, capture the failure and suggest the next narrowest recovery action.
