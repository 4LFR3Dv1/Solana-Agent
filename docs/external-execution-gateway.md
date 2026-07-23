# External execution gateway

Work item `SA-GW-001` introduces a transport boundary around Solana-Agent
without moving or modifying its existing execution kernel. The gateway is
versioned JSONL over stdin/stdout, with one response for each non-empty input
line.

`SA-GW-001` implements transport, dispatch, durability, and replay safety.
`SA-GW-002` adds an optional devnet SPL preparation backend. It materializes
and simulates a `TransferChecked` message but cannot sign or broadcast it.

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

Without `--signer`, the backend fails closed. Enable preparation with a public
key only:

```bash
solana-agent-gateway \
  --journal .solana-agent/gateway.sqlite3 \
  --signer SOURCE_OWNER_PUBLIC_KEY
```

The configured signer must equal the economic source. It is only an account
meta and fee payer in the prepared message. Private keys, keypair paths,
signatures, and broadcast commands are not accepted.

The `prepare` payload contains the Foundry `ExternalExecutionRequest` plus
closed local constraints:

```json
{
  "request": {
    "type": "external_execution_request",
    "protocol_version": "1.0.0",
    "execution_request_id": "exec_01",
    "idempotency_key": "idem_01",
    "economic_plan": {},
    "economic_plan_hash": "sha256:...",
    "economic_approval": {}
  },
  "preparation_context": {
    "constraints": {
      "max_fee_lamports": 50000,
      "allowed_programs": [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
      ]
    }
  }
}
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
default backend fails closed with `backend_not_configured`. Passing a public
`--signer` selects `SolanaPreparationBackend`. The only supported network and
capability are `solana:devnet` and `solana.spl_transfer.v1`; the only permitted
program is the canonical SPL Token program.

The gateway request hash uses deterministic JSON only to protect the local
transport idempotency key. It is not the economic `plan_hash`, JCS domain
normalization, a prepared Solana message hash, or an execution commitment. The
preparation backend independently applies RFC 8785 to the economic plan,
simulation attestation, and execution commitment. The prepared message hash is
SHA-256 over the exact serialized versioned message bytes.

The economic source and destination are wallet owners. Associated token
accounts are derived deterministically. Preparation verifies mint ownership,
decimals, token-account owners, source balance, RPC genesis hash, recent
blockhash, fee, and simulation result.

## Expiry and recovery

A preparation expires at the earliest of the economic-plan expiry, 60 seconds
after simulation, or the blockhash's last valid block height. Any expired
message requires a new `execution_request_id`, blockhash, simulation,
commitment, and future execution authorization.

Because SA-GW-002 never broadcasts, recovery can only report
`failed_before_broadcast`. Rematerialization is allowed only after the previous
preparation is proven expired.
