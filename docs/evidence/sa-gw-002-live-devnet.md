# SA-GW-002 live devnet proof

On 2026-07-23, the JSONL gateway prepared and simulated a real SPL
`TransferChecked` operation against a persistent Solana devnet fixture. It did
not sign or broadcast the prepared operation.

## Result

- Local policy: `allow`
- Simulation: successful at slot `478362693`
- Fee: `5000` lamports
- Compute units: `105`
- Exact prepared message:
  `sha256:85a6b98ca7c050ee9dcba7aa0750d876a8ef5fd084458e768200256a9950cba6`
- Simulation attestation:
  `sha256:8ea92ac3e6cf487989c3292d6b67059be5fc32b5b3e13086a6d648e082efb75f`
- Execution commitment:
  `sha256:e5e6ec585f0e46a6a77a2c07c291d9bb1b0c02c9c375297abdf739b00421ee79`

The identical gateway request returned the durable response with
`replayed: true`. A new process then opened the same SQLite journal and
successfully returned `status`, `recover`, and `evidence`. Recovery reported
`failed_before_broadcast` and prohibited rematerialization while the prepared
message remained valid.

## Persistent fixture

- [Mint on Solana Explorer](https://explorer.solana.com/address/2tUzxADKHWxwTpihHuuzwfoGhYBY7735s2QXEuUcNX3k?cluster=devnet)
- [Source token account](https://explorer.solana.com/address/ACMsvvFpaafhg3477mgTu3ovgrhFLn9VMTB6Nzp65mnf?cluster=devnet)
- [Destination token account](https://explorer.solana.com/address/EUt7wV4f5bzSFJccZ5aafJV7zykZfyWz9rctaug7hVxd?cluster=devnet)
- [Create mint transaction](https://explorer.solana.com/tx/5kreZZVAevXxAyZEFPp5yCeLrKD5q8NtgPN18SYgyDdPBCb31DYSMTWuuopkjWQn63Kk4fUirQWmDrxpQX2X6JBJ?cluster=devnet)
- [Create token accounts transaction](https://explorer.solana.com/tx/5RY34WsapUMeh3pfyTo6f7qPpQLyKr6BXLNiP74RnDFEMKdFaYm2m9PddWgpuv6kbV6AVwpgwXjLr3rrNg4cuWsh?cluster=devnet)
- [Mint supply transaction](https://explorer.solana.com/tx/3yVF5dUKwVCAJVGawuaZeWQgPYCVQguXsuZbZ6VSAp5rCSZkPmFD5FLYB84QfR5VwHhWVrx35wEQfAD1a3D1VMs1?cluster=devnet)

An independent `getMultipleAccounts` query at slot `478362811` observed
`100000000` base units at the source and `0` at the destination. All three
fixture transactions were independently reported as finalized with no error.
The structured evidence is in
[`sa-gw-002-live-devnet.json`](./sa-gw-002-live-devnet.json).

## Live-discovered regression

The first live simulation exposed the valid Solana RPC sentinel
`rentEpoch = 18446744073709551615`, which is outside the JCS safe-integer
domain. RPC observations are now normalized by converting integers to decimal
strings before hashing. External Foundry protocol objects remain strict, and
unsafe JSON numbers are still rejected.
