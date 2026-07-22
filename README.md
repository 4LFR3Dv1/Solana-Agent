# Solana Agent Runtime

Solana Agent Runtime is an open-source execution, safety, and evaluation layer
for coding agents that build on Solana. It turns local Solana and Anchor
workflows into governed, reproducible, evidence-backed runs.

The project is currently **pre-alpha**. Its architecture and initial mission
runner exist, but the complete devnet workflow has not yet been validated end
to end. See the [development plan](docs/solana-agent-development-plan.md) for
the implementation sequence and acceptance gates.

## Scope

This repository currently defines:

- the agent contract in `agent.md`
- Solana/Anchor skills in `skills/`
- mission flows in `missions/`
- structured schemas in `contracts/`
- deterministic execution adapters in `scripts/solana/`
- local runtime state in `.solana-agent/`
- a transactional command journal in `solana_agent/execution/`
- fail-closed policy and bound approvals in `solana_agent/authority/`

The target runtime will complement coding agents, Solana Developer MCP,
Anchor, and Solana CLI. Coding agents may propose work; the runtime remains
responsible for policy, approvals, execution, journaling, and verification.

## Target MVP

The first supported mission is `create-counter`, which should:

1. check the local Solana/Anchor environment
2. scaffold an Anchor counter workspace
3. run the test flow `initialize > increment`
4. deploy to devnet
5. invoke the deployed program
6. capture Program ID, transaction signatures, and Explorer links
7. generate an evidence pack

Success must be independently verifiable from the command journal, artifacts,
Solana RPC state, and transaction signatures. A generated link alone is not
considered evidence.

## Installation

Python 3.11 or newer is required for the runtime core.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.venv\Scripts\Activate.ps1
```

The Python core and its tests do not require Solana, Anchor, Rust, a wallet,
WSL, or network access. Those dependencies are only required by integration
and mission execution flows.

## Usage

Inspect the local environment:

```bash
python -m solana_agent doctor
python -m solana_agent inspect-env
```

Host preflight on Windows reports WSL readiness and installed host tools. `inspect-env` is the runtime-level check and expects a usable bash environment.

```bash
python -m solana_agent inspect-env
```

Run the MVP mission:

```bash
python -m solana_agent run create-counter \
  --workspace /path/to/counter \
  --project-name counter \
  --approve-airdrop \
  --approve-deploy
```

On Windows, the runtime defaults to WSL for Solana and Anchor commands.
The WSL distro and user can be configured with `SOLANA_AGENT_WSL_DISTRO` and `SOLANA_AGENT_WSL_USER`.

## Development

Install the development tools:

```bash
python -m pip install -e ".[dev]"
```

Run the local quality gates:

```bash
ruff check .
mypy solana_agent
pytest
python -m compileall -q solana_agent
```

The same checks run in CI on supported Python versions. Contribution rules and
runtime invariants are documented in [CONTRIBUTING.md](CONTRIBUTING.md).

## Repository Layout

```text
solana-agent/
  agent.md
  solana_agent/
  tests/
  skills/
  missions/
  prompts/
  templates/
  contracts/
  scripts/solana/
  examples/
  docs/
```

## Runtime State

Runtime artifacts are stored in `.solana-agent/`, which is local-only and ignored by Git.

## Status

The repository contains an initial hardcoded mission runner, executable
contracts, a transactional SQLite journal, versioned policy profiles, bound
single-use approvals, Solana/Anchor adapters, and a renderable counter template.
The next development priority is the declarative mission engine before the
reproducible Solana toolchain and devnet proof.

## License and provenance

Licensed under the [Apache License 2.0](LICENSE). Architectural provenance and
future third-party attributions are recorded in [NOTICE.md](NOTICE.md).
