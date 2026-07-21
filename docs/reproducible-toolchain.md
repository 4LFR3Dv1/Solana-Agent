# Reproducible Solana toolchain

PR4 replaces shell-script assumptions with a pinned Linux environment and
structured production adapters. `toolchain.lock.json` is the executable source
of truth for every version probe.

## Pinned baseline

| Component | Version |
| --- | --- |
| Ubuntu | 22.04 (digest pinned) |
| Python | 3.11.9 |
| Rust and Cargo | 1.97.1 |
| Agave CLI and local validator | 4.1.2 |
| Anchor CLI | 1.1.2 |
| Node.js | 22.22.3 |
| pnpm | 10.28.0 |

The final image remains Ubuntu-based. Python is copied from the digest-pinned
`python:3.11.9-slim-bullseye` build stage; all other installers receive exact
versions, and Node's archive is SHA-256 verified.

## Start the environment

From a clean machine with Docker Desktop or Docker Engine running:

```bash
docker compose -f environment/compose.yaml build
docker compose -f environment/compose.yaml up -d
docker compose -f environment/compose.yaml exec solana-agent bash
```

Inside the container, verify the contract and install the runtime:

```bash
python3 environment/verify-toolchain.py toolchain.lock.json
python3 -m pip install -e '.[dev]'
pytest
```

VS Code-compatible clients can instead open `environment/devcontainer.json`.

## Adapter boundary

`build_adapter_registry(AdapterConfig(...))` returns real executors for:

- host/toolchain diagnostics;
- root-confined filesystem operations;
- Anchor scaffold, build, test, key inspection, and deploy;
- locked pnpm install with lifecycle scripts disabled, plus approval-gated named scripts;
- allowlisted Solana CLI operations;
- allowlisted JSON-RPC reads.

Every external process receives a structured argument list and runs with
`shell=False`. The process runner restricts inherited environment variables,
caps captured output, records the effective argv and cwd, and converts timeouts
and interruptions into journal-compatible failures. The Policy Engine remains
default-deny and requires a bound approval for both Solana CLI and Anchor
deployments.

`build_governed_runtime()` connects this registry to the persistent command
journal and declarative mission engine.

## Integration validation

Fast tests replace binaries with recording transports and verify exact command
arguments. The manually triggered `Pinned toolchain integration` workflow then:

1. builds the development image;
2. verifies all tool versions;
3. builds `tests/fixtures/anchor-workspace` with Anchor;
4. starts a real local validator and calls `getVersion` through the RPC adapter.

The integration test is intentionally opt-in locally:

```bash
SOLANA_AGENT_RUN_INTEGRATION=1 pytest tests/test_local_validator_integration.py
```

This repository's current Windows host does not have a running Docker daemon or
a WSL Linux distribution. Therefore the local handoff can verify the image
definition and unit contracts, while the Linux workflow is the authoritative
real-toolchain gate.
