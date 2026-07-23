# SA-CHAOS-001: real-process failure boundaries

SA-CHAOS-001 adds explicit, test-only kill points to the external execution
gateway. The production default has no chaos hook. A kill point can be enabled
only through an explicit CLI argument and a localhost RPC proxy requires a
separate opt-in flag.

## Safety property

The controlled runtime provides:

> At-most-one `sendTransaction` request, with the exact signature and signed
> transaction persisted before the broadcast intent, signature-first recovery,
> and no automatic rematerialization under uncertainty.

This is not a claim of exactly-once blockchain execution.

## Kill points

- `after_execution_validated_before_claim`
- `after_signature_and_broadcast_intent_persisted`
- `before_send_transaction`
- `after_send_transaction_response_before_persist`

When configured, `ProcessKillHook` atomically writes a public sentinel containing
the kill point, execution request ID, and transaction signature, then exits the
process with code 86. It never writes key material.

Example:

```text
solana-agent-gateway \
  --journal .solana-agent/chaos.sqlite3 \
  --signer PUBLIC_KEY \
  --foundry-authority PUBLIC_KEY \
  --rpc-endpoint http://127.0.0.1:18899 \
  --allow-test-rpc-proxy \
  --chaos-kill-point after_signature_and_broadcast_intent_persisted \
  --chaos-sentinel .solana-agent/kill.json
```

Noncanonical RPC endpoints remain rejected unless all of these conditions hold:

- `--allow-test-rpc-proxy` is present;
- scheme is plain HTTP;
- hostname is `127.0.0.1`, `localhost`, or `::1`.

The restriction prevents the chaos surface from becoming a general RPC
allowlist bypass.

## Durable claim

`ExecutionStore.claim` runs under `BEGIN IMMEDIATE` with WAL and a busy timeout.
It persists:

- execution request and authorization IDs;
- exact prepared-message and commitment hashes;
- signer and signature;
- complete signed transaction;
- `broadcast_started`;
- `broadcast_count = 1`;
- signature timestamp.

Two processes sharing the same journal contend for this claim. One wins; the
other receives `needs_recovery` or an already-started result. Only the winner can
reach `sendTransaction`.

## Definitive rejection

A JSON-RPC error response to `sendTransaction` is distinguished from a transport
failure:

- JSON-RPC rejection: durable execution state becomes `failed` with
  `definitive_rejection`;
- timeout, socket close, lost response, or malformed result: durable state
  becomes `needs_recovery`.

Neither outcome authorizes a new economic effect. Foundry remains responsible
for reconciliation and any future remediation decision.

## Coordinated matrix

The Foundry Pay FP-FAIL-002 harness launches this gateway over JSONL, a
persistent fault-injection proxy, and an emulated Solana RPC upstream as
independent subprocesses. `gateway.chaos_scenario` owns only ephemeral fixture
keys in memory and emits sanitized results.

The coordinated matrix covers:

1. kill before the durable execution claim;
2. kill after signature and broadcast-intent persistence;
3. kill while the RPC response is in flight;
4. response loss after upstream acceptance;
5. definitive pre-acceptance rejection;
6. signature absent after blockhash expiry;
7. replay after restart;
8. two concurrent gateway processes.

Source availability and convergence are evaluated by the Foundry reconciler as
the ninth scenario.
