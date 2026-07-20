#!/usr/bin/env bash
set -euo pipefail

program_id="${1:-}"
signature="${2:-}"
cluster="${3:-devnet}"

if [ -z "$program_id" ] || [ -z "$signature" ]; then
  echo '{"ok": false, "error": "usage: capture_evidence.sh <program_id> <signature> [cluster]"}'
  exit 1
fi

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

explorer_tx="https://explorer.solana.com/tx/${signature}?cluster=${cluster}"
explorer_program="https://explorer.solana.com/address/${program_id}?cluster=${cluster}"

cat <<EOF
{
  "ok": true,
  "program_id": $(json_string "$program_id"),
  "signature": $(json_string "$signature"),
  "cluster": $(json_string "$cluster"),
  "explorer_program": $(json_string "$explorer_program"),
  "explorer_tx": $(json_string "$explorer_tx")
}
EOF
