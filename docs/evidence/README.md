# Public evidence

This directory is the public claim boundary for the current Solana-Agent and
Foundry Pay proof. It separates live Solana observations from deterministic
execution tests and real-process fault injection.

## Proof chain

```text
Foundry economic plan
→ Solana-Agent prepares exact SPL message
→ live devnet simulation
→ Foundry authorizes the exact commitment
→ isolated signer signs only those bytes
→ Solana-Agent persists signature and broadcasts once
→ finalized devnet transaction
→ Foundry observes matching L1/L2 results
→ process-failure recovery proof
```

## Evidence ledger

| Proof | Environment | Result | Artifact |
|---|---|---|---|
| SA-GW-002 | live Solana devnet | SPL `TransferChecked` prepared and simulated; replay/status/recover survived restart | [`sa-gw-002-live-devnet.md`](sa-gw-002-live-devnet.md), [`json`](sa-gw-002-live-devnet.json) |
| SA-EXEC-001 | deterministic RPC | exact authorization and Solana signatures verified; signature durable before one send; recovery never rebroadcasts | [`sa-exec-001.md`](sa-exec-001.md) |
| FP-E2E-001 | live Solana devnet | governed transfer finalized at slot `478403722`; balance deltas matched `1,000,000` base units | [Solana Explorer](https://explorer.solana.com/tx/RzgQYATtgFZNG7eDgktPAaKh3R922BEjYNLRnvM7u96eFjsnSe4aFYQAtgaP4Hi7kyn91itF1eTEeo498NJ8uS4?cluster=devnet) |
| FP-REC-001 | two live devnet RPC providers | both normalized observations matched; consensus approved | [`public-system-proof-v1.json`](public-system-proof-v1.json) |
| SA-CHAOS-001 / FP-FAIL-002 | real OS processes, deterministic upstream | modeled failures stayed fail-closed; every proxy send count was at most one | [`chaos-testing.md`](../chaos-testing.md), [`public-system-proof-v1.json`](public-system-proof-v1.json) |

## Exact live bindings

```text
economic_plan_hash
sha256:43c3fb1d1ba9d76127ccc9452be833c8265db57f9dd2d27e7e95afeab2761a33

prepared_message_hash
sha256:1aac4e92ecb84e91ac69d34d5f1f7040ff6ffde2988477e6b7eff6d5fc341d9b

simulation_attestation_hash
sha256:e81a50bfa4a4f84718e3fea5a51b4bdd4ee8bad95448add52c0a6fc6855ee7a0

execution_commitment_hash
sha256:1b79470062864179b011b5803843389f574d998d30e7afbf3a9fcb3962cbf8ff
```

## Source-diverse reconciliation

Foundry Pay queried the finalized transaction through two distinct devnet
provider boundaries. Each response was independently normalized and hashed:

```text
L1  sha256:20147b7ceb7fa8190a9f354c6d7d36707e716ba20d8aace3eb3f8508817a2cf4
L2  sha256:e465874ba90be369df1616edd226a083a0aaa5797001e4d4595f753971ac4121
```

Both observations matched the obligation. The credential-bearing L2 endpoint
was not persisted. This proves source diversity, not an L3 or institutionally
independent attestation.

## Canonical recovery demonstration

The public snapshot records the result of the coordinated process test:

```text
RPC upstream accepts the transaction
→ proxy persists the upstream response
→ proxy drops the client response
→ gateway enters needs_recovery
→ gateway restarts
→ recover queries the persisted signature
→ recovered_confirmed
```

```text
sendTransaction requests received = 1
upstream requests forwarded       = 1
client responses delivered        = 0
rebroadcasts                      = 0
```

The full generated scenario bundle is maintained in the companion
Foundry Pay repository. Its sanitized public checkpoint is mirrored in
[`public-system-proof-v1.json`](public-system-proof-v1.json).

## Verification boundary

Public evidence contains transaction references, public hashes, implementation
commits, normalized outcomes, and sanitized process counters. It excludes:

- private keys and seed phrases;
- Foundry authorization secrets;
- credential-bearing RPC endpoints;
- raw chaos-fixture transaction bytes;
- customer or production data.

The defensible execution property is:

> At-most-one broadcast by the controlled runtime, with signature-first
> recovery and no automatic rematerialization while the economic outcome is
> unknown.

This evidence does not claim exactly-once blockchain execution, arbitrary
failure tolerance, mainnet readiness, production custody, L3 verification, or
an external security audit.
