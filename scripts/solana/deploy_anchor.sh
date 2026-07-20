#!/usr/bin/env bash
set -euo pipefail

workspace_path="${1:-}"
program_name="${2:-}"

if [ -z "$workspace_path" ] || [ -z "$program_name" ]; then
  echo '{"ok": false, "error": "usage: deploy_anchor.sh <workspace_path> <program_name>"}'
  exit 1
fi

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

pushd "$workspace_path" >/dev/null
deploy_output="$(anchor deploy 2>&1)" || {
  popd >/dev/null
  cat <<EOF
{
  "ok": false,
  "workspace_path": $(json_string "$workspace_path"),
  "program_name": $(json_string "$program_name"),
  "error": $(json_string "$deploy_output")
}
EOF
  exit 1
}
popd >/dev/null

program_id="$(printf '%s\n' "$deploy_output" | awk '/Program Id:/ {print $3}' | tail -n 1)"
deploy_signature="$(printf '%s\n' "$deploy_output" | awk '/Signature:/ {print $2}' | tail -n 1)"

cat <<EOF
{
  "ok": true,
  "workspace_path": $(json_string "$workspace_path"),
  "program_name": $(json_string "$program_name"),
  "program_id": $(json_string "$program_id"),
  "deploy_signature": $(json_string "$deploy_signature"),
  "output": $(json_string "$deploy_output")
}
EOF
