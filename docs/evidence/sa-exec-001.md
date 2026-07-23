# SA-EXEC-001 execution and recovery evidence

Date: 2026-07-23

Implementation commit:
`dfe1f2e6757374f606e7a27f33b771d44fd2925b`.

## Result

The external JSONL gateway now supports `authorize-and-execute`. The execution
backend:

1. loads the persisted `PreparedExecution`;
2. checks time and blockhash validity;
3. verifies the Foundry Ed25519 signature over the JCS-canonical unsigned
   `ExecutionAuthorization`;
4. verifies request, commitment, exact-message hash, signer, and single-use
   bindings;
5. verifies the Solana signature over the exact persisted message bytes;
6. constructs the signed transaction;
7. durably stores authorization, signature, signed transaction, and
   `broadcast_started`;
8. calls `sendTransaction` once with RPC retries disabled;
9. persists the returned signature and technical receipt before returning.

`status`, `recover`, and `evidence` query or expose the persisted signature.
They never reconstruct or rebroadcast a transaction. A response lost after
broadcast is recovered by `getSignatureStatuses` without a second
`sendTransaction`.

An absent signature after blockhash expiry remains
`not_found_after_expiry_needs_reconciliation` with
`may_rematerialize = false`. One RPC provider is insufficient to authorize a
new economic effect.

## Verification

```text
python -m ruff check .
All checks passed!

python -m mypy solana_agent gateway
Success: no issues found in 49 source files

python -m pytest
150 passed, 2 skipped in 8.07s

python -m compileall -q solana_agent gateway
passed

git diff --check
passed
```

The two skipped tests are existing opt-in local-validator/toolchain integration
tests. The SA-EXEC-001 suite itself ran completely.

## Acceptance

- Foundry authorization authenticity is independently verified: passed.
- Exact prepared message and Solana signature are verified: passed.
- Signature is durable before RPC broadcast begins: passed.
- RPC receives the exact signed prepared transaction: passed.
- `sendTransaction` is called once with `maxRetries = 0`: passed.
- Authorization, message, and signature tampering fail before broadcast:
  passed.
- Expired authorization or blockhash fails before broadcast: passed.
- Concurrent/replayed execution cannot broadcast again: passed.
- Lost response recovers by persisted signature across restart: passed.
- Confirmed status and receipt persist across restart: passed.
- Evidence exposes preparation, authorization, signature, submission, chain
  observation, and receipt: passed.
- Missing history after expiry does not permit automatic rematerialization:
  passed.

## Residual gate

This evidence uses a deterministic RPC double and ephemeral test keypairs. It
does not claim a live devnet transfer. The next release gate is one funded
devnet fixture executed through Foundry authorization and the signer boundary,
followed by independent reconciliation of the resulting signature.
