#!/usr/bin/env bash
set -euo pipefail

amount="${1:-1}"
wallet="${2:-}"

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

cmd=(solana airdrop "$amount" --url devnet)
if [ -n "$wallet" ]; then
  cmd=(solana airdrop "$amount" "$wallet" --url devnet)
fi

output="$("${cmd[@]}" 2>&1)" || {
  cat <<EOF
{
  "ok": false,
  "command": $(json_string "${cmd[*]}"),
  "error": $(json_string "$output")
}
EOF
  exit 1
}

cat <<EOF
{
  "ok": true,
  "command": $(json_string "${cmd[*]}"),
  "output": $(json_string "$output")
}
EOF
