# Contributing to Solana Agent Runtime

Solana Agent Runtime is currently pre-alpha. Contributions should preserve its
local-first, evidence-first, and fail-closed design.

## Development setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

The core test suite must not require Solana CLI, Anchor, Rust, a wallet, WSL,
or network access.

## Quality checks

Run all checks before proposing a change:

```bash
ruff check .
mypy solana_agent
pytest
python -m compileall -q solana_agent
```

## Operational invariants

Changes to the runtime must preserve these rules:

- persist command intent before validation or execution;
- preserve rejected and failed operations in the journal;
- do not infer approvals;
- use allowlisted adapters and structured arguments;
- do not use `shell=True` in the governed executor;
- never persist seed phrases or private keys;
- keep mainnet operations disabled during the MVP;
- do not report mission success without verified evidence;
- update contracts and documentation when behavior changes.

## Scope and provenance

Keep changes small and focused. Concepts adapted from SNE Foundry or code
derived from any other project must retain clear provenance and compatible
licensing in `NOTICE.md`.

## Pull requests

A pull request should include:

- the problem being solved;
- the contract or invariant affected;
- tests covering success and failure paths;
- validation commands and their results;
- residual risks or known limitations;
- documentation updates when behavior changes.
