#!/usr/bin/env bash
set -euo pipefail

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

version_or_null() {
  local cmd="$1"
  if command_exists "$cmd"; then
    "$cmd" --version 2>/dev/null | head -n 1
  else
    printf 'null'
  fi
}

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

solana_config_raw=""
wallet_address=""
cluster="unknown"
keypair_path=""

if command_exists solana; then
  solana_config_raw="$(solana config get 2>/dev/null || true)"
  cluster="$(printf '%s\n' "$solana_config_raw" | awk -F': ' '/RPC URL/ {print $2}' | tail -n 1)"
  keypair_path="$(printf '%s\n' "$solana_config_raw" | awk -F': ' '/Keypair Path/ {print $2}' | tail -n 1)"
  wallet_address="$(solana address 2>/dev/null || true)"
fi

rust_version="$(version_or_null rustc)"
solana_version="$(version_or_null solana)"
anchor_version="$(version_or_null anchor)"
node_version="$(version_or_null node)"
yarn_version="$(version_or_null yarn)"

ready=true
for item in "$rust_version" "$solana_version" "$anchor_version" "$node_version" "$yarn_version"; do
  if [ "$item" = "null" ]; then
    ready=false
  fi
done

cat <<EOF
{
  "ok": true,
  "ready": $ready,
  "rustc_version": $(json_string "$rust_version"),
  "solana_version": $(json_string "$solana_version"),
  "anchor_version": $(json_string "$anchor_version"),
  "node_version": $(json_string "$node_version"),
  "yarn_version": $(json_string "$yarn_version"),
  "cluster": $(json_string "$cluster"),
  "keypair_path": $(json_string "$keypair_path"),
  "wallet_address": $(json_string "$wallet_address")
}
EOF
