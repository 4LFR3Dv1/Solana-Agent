# External execution gateway

Work item `SA-GW-001` introduces a transport boundary around Solana-Agent
without moving or modifying its existing execution kernel. The gateway is
versioned JSONL over stdin/stdout, with one response for each non-empty input
line.

`SA-GW-001` implements transport, dispatch, durability, and replay safety.
`SA-GW-002` adds an optional devnet SPL preparation backend. It materializes
and simulates a `TransferChecked` message but cannot sign or broadcast it.
`SA-EXEC-001` adds a signature-first execution layer without moving or
modifying the Solana-Agent kernel.

## Commands

The version 1 command set is closed:

- `prepare`
- `authorize-and-execute`
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
meta and fee payer in the prepared message. Private keys and keypair paths are
never accepted.

Execution additionally requires the public Ed25519 identity of the Foundry
authorization authority:

```bash
solana-agent-gateway \
  --journal .solana-agent/gateway.sqlite3 \
  --signer SOURCE_OWNER_PUBLIC_KEY \
  --foundry-authority FOUNDRY_AUTHORIZATION_PUBLIC_KEY
```

The authority key verifies the JCS-canonical unsigned
`ExecutionAuthorization`. It cannot sign Solana transactions.

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
The effectful `authorize-and-execute` command persists the Solana signature,
signed transaction, authorization, and `broadcast_started` state before its
single `sendTransaction` call. It sets RPC `maxRetries` to zero. If the RPC
response is lost, the state becomes `needs_recovery`; neither transport replay
nor a new gateway request can broadcast again.

## Backend boundary

`ExternalExecutionBackend` defines five methods matching the command set. The
default backend fails closed with `backend_not_configured`. Passing a public
`--signer` selects `SolanaExecutionBackend`, which preserves the preparation
behavior and fails execution closed until `--foundry-authority` is configured.
The only supported network and capability are `solana:devnet` and
`solana.spl_transfer.v1`; the only permitted program is the canonical SPL Token
program.

The gateway request hash uses deterministic JSON only to protect the local
transport idempotency key. It is not the economic `plan_hash`, JCS domain
normalization, a prepared Solana message hash, or an execution commitment. The
preparation backend independently applies RFC 8785 to the economic plan,
simulation attestation, and execution commitment. The prepared message hash is
SHA-256 over the exact serialized versioned message bytes.

The economic source and destination are wallet owners. Associated token
accounts are derived deterministically. Preparation verifies mint ownership,
decimals, token-account owners, source balance, RPC genesis hash, recent
blockhash, fee, and simulation result. Source and destination owners must
differ, preventing the two writable token-account roles from aliasing.

RPC simulation observations are normalized separately from external protocol
objects before hashing: every observed integer becomes its unsigned decimal
string representation. This accommodates valid Solana `u64` fields such as
`rentEpoch = 18446744073709551615` without weakening the JCS safe-integer
rejection applied to Foundry requests. RPC floats and unsupported values remain
invalid.

## Expiry and recovery

A preparation expires at the earliest of the economic-plan expiry, 60 seconds
after simulation, or the blockhash's last valid block height. Any expired
message requires a new `execution_request_id`, blockhash, simulation,
commitment, and future execution authorization.

Before execution, the agent independently checks authorization time bounds,
prepared-message expiry, and blockhash height. It verifies the Foundry
authorization signature and the Solana signature over the exact stored message
bytes. It also requires the message fee payer and sole required signer to equal
the configured signer.

After broadcast, `status` and `recover` query
`getSignatureStatuses(signature, searchTransactionHistory=true)`. A confirmed
or failed observation is persisted with the technical receipt. A missing
signature remains ambiguous while the blockhash is live. Only when the
signature is still absent after the last valid block height can recovery report
`not_found_after_expiry_needs_reconciliation`. This does not authorize
rematerialization: Foundry must independently reconcile the obligation before
any new preparation.

`evidence` returns the preparation, authorization, persisted signature, signed
transaction, RPC submission response, chain observation, and current technical
receipt. These artifacts are executor evidence; Foundry remains responsible for
independent reconciliation and business success.

### `authorize-and-execute` payload

```json
{
  "execution_request_id": "exec_01",
  "prepared_message_base64": "...",
  "execution_authorization": {
    "type": "execution_authorization",
    "protocol_version": "1.0.0",
    "authorization_id": "auth_01",
    "execution_request_id": "exec_01",
    "execution_commitment_hash": "sha256:...",
    "prepared_message_hash": "sha256:...",
    "signer": "...",
    "single_use": true,
    "issued_at": "2026-07-23T16:00:00Z",
    "expires_at": "2026-07-23T16:00:30Z",
    "authorization_signature": "BASE58_ED25519_SIGNATURE"
  },
  "message_signature": {
    "signer": "...",
    "signature": "BASE58_SOLANA_SIGNATURE"
  }
}
```

The payload is closed. The prepared message must be byte-identical to the
persisted preparation. Adding the command is an additive gateway capability;
the versioned envelope and all existing command shapes remain unchanged at
`1.0.0`.

Real-process fault injection and the localhost-only RPC proxy boundary are
specified in `docs/chaos-testing.md`. Chaos hooks are absent by default and
cannot enable arbitrary remote RPC endpoints.
