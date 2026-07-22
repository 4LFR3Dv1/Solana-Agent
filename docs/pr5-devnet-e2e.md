# PR5 — Devnet end-to-end proof

PR5 turns `create-counter` into the first governed proof of the product's core
claim. The runtime, rather than an operator shell script, owns execution order,
policy decisions, approvals, persistence, RPC verification, and evidence export.

## Delivered flow

```text
doctor → anchor scaffold → apply counter template → install → build → test
       → require pre-funded balance
       → approve deploy  → deploy
       → approve invoke  → initialize + increment
       → verify RPC      → evidence.json
```

The CI proof restores a dedicated, pre-funded signer from a masked GitHub
Actions secret. The mission checks a reusable minimum balance of 2 SOL before
allowing deployment. The private key is written only inside the ephemeral
container and is never uploaded as an artifact.

The mission pauses independently at every material operation. An approval is
bound to the exact command and policy input hash, expires, is single-use, and is
recorded in the journal. Resume skips successful steps and continues from the
pending command.

## Runtime contract

Create a local, ignored JSON file. `workspace_root` must contain the new Anchor
workspace, `wallet` is the public address of the Solana CLI signer, and no
private key or seed phrase belongs in this file.

```json
{
  "id": "devnet-proof",
  "version": "1.0.0",
  "policy_profile": "devnet-safe",
  "workspace_root": "/workspace/proofs",
  "cluster": "devnet",
  "wallet": "YOUR_PUBLIC_SOLANA_ADDRESS",
  "max_lamports": 2000000000,
  "tool_versions": {
    "solana": "4.1.2",
    "anchor": "1.1.2",
    "pnpm": "10.28.0"
  }
}
```

The configured Solana CLI wallet must be available to Anchor in the execution
environment. The runtime persists only its public address.

## Execute and resume

```bash
solana-agent missions start create-counter \
  --contract runtime.devnet.json \
  --input workspace=/workspace/proofs/counter-demo \
  --input project_name=counter-demo
```

When the result is `waiting_approval`, inspect the complete bound request:

```bash
solana-agent approvals list RUN_ID --contract runtime.devnet.json
solana-agent approvals approve APPROVAL_ID \
  --by operator@example \
  --contract runtime.devnet.json
solana-agent missions resume RUN_ID --contract runtime.devnet.json
```

Repeat the decision and resume sequence for deploy and invoke. A
denied, expired, or altered approval fails closed.

To inspect a failed command with its persisted stdout and stderr:

```bash
solana-agent commands list RUN_ID --contract runtime.devnet.json \
  --failed-only --include-output
```

## Evidence contract

The invoke script emits machine-readable markers for:

- Program ID;
- counter account public key;
- deploy signature captured from Anchor;
- initialize signature;
- increment signature;
- observed counter value.

The evidence adapter does not trust those claims alone. Against the allowlisted
RPC endpoint it confirms:

- the Program ID exists and is executable;
- both transaction signatures succeeded at confirmed or finalized commitment;
- the counter account is owned by that program;
- the Anchor account payload encodes the expected `u64` count.

Only after every assertion passes is the bundle written to
`WORKSPACE/.solana-agent/evidence/RUN_ID/evidence.json` and duplicated into the
transactional artifact store with its SHA-256 hash.

## Independent verification

Another operator can verify public identifiers without the original workspace
or deployment run:

```bash
solana-agent missions start verify-devnet-deploy \
  --contract runtime.devnet.json \
  --input program_id=PROGRAM_ID \
  --input counter_pubkey=COUNTER_ACCOUNT \
  --input deploy_signature=DEPLOY_SIGNATURE \
  --input initialize_signature=INITIALIZE_SIGNATURE \
  --input increment_signature=INCREMENT_SIGNATURE \
  --input expected_count=1
```

This mission performs a fresh program lookup and the same RPC evidence checks.

## Acceptance gate

Code-level acceptance requires unit coverage for template materialization, RPC
state verification, evidence export, approval/resume, failure persistence, and
idempotent resume. The live devnet acceptance remains explicit: publish one
clean run's Program ID, signatures, evidence hash, and short recording. No
fixture or mocked identifier may be presented as live proof.
