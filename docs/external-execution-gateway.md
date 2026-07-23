# External execution gateway

Work item `SA-GW-001` introduces a transport boundary around Solana-Agent
without moving or modifying its existing execution kernel. The gateway is
versioned JSONL over stdin/stdout, with one response for each non-empty input
line.

This work item implements transport, dispatch, durability, and replay safety.
It deliberately does **not** materialize, simulate, sign, or broadcast Solana
transactions. `SA-GW-002` will connect the backend boundary to local policy and
Solana preparation.

## Commands

The version 1 command set is closed:

- `prepare`
- `status`
- `recover`
- `evidence`

Each request has exactly four fields:

```json
{"gateway_protocol_version":"1.0.0","gateway_request_id":"gw_01","command":"prepare","payload":{"execution_request_id":"exec_01"}}
```

Start the process with:

```bash
solana-agent-gateway --journal .solana-agent/gateway.sqlite3
```

Stdout is reserved for compact protocol responses. Diagnostics and application
logs must never be written there.

## Durability and idempotency

The independent SQLite gateway journal reserves `gateway_request_id` before
backend dispatch and persists the complete response before stdout is flushed.

- Repeating the same ID with the same request returns the persisted response
  with `replayed: true`.
- Repeating the same ID with different input returns
  `idempotency_conflict`.
- Finding a reserved request without a durable response returns
  `needs_recovery`; the gateway never redispatches it automatically.

This is transport idempotency, not a claim of blockchain `exactly once`.
Future effectful commands must persist a signature before replying and resolve
ambiguous broadcast state through recovery.

## Backend boundary

`ExternalExecutionBackend` defines four methods matching the command set. The
default backend fails closed with `backend_not_configured`. Tests inject a
deterministic backend to prove routing and recovery without implying that
Solana execution exists.

The gateway request hash uses deterministic JSON only to protect the local
transport idempotency key. It is not the economic `plan_hash`, JCS domain
normalization, a prepared Solana message hash, or an execution commitment.
Those protocol objects remain explicit backend inputs in subsequent work.
