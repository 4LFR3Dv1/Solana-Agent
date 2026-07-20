#!/usr/bin/env bash
set -euo pipefail

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

config_raw="$(solana config get 2>/dev/null || true)"
cluster="$(printf '%s\n' "$config_raw" | awk -F': ' '/RPC URL/ {print $2}' | tail -n 1)"
keypair_path="$(printf '%s\n' "$config_raw" | awk -F': ' '/Keypair Path/ {print $2}' | tail -n 1)"
wallet_address="$(solana address 2>/dev/null || true)"

cat <<EOF
{
  "ok": true,
  "cluster": $(json_string "$cluster"),
  "keypair_path": $(json_string "$keypair_path"),
  "wallet_address": $(json_string "$wallet_address")
}
EOF
