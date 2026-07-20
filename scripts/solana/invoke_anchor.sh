#!/usr/bin/env bash
set -euo pipefail

workspace_path="${1:-}"
command_name="${2:-test}"

if [ -z "$workspace_path" ]; then
  echo '{"ok": false, "error": "usage: invoke_anchor.sh <workspace_path> [command_name]"}'
  exit 1
fi

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

pushd "$workspace_path" >/dev/null
output="$(anchor "$command_name" 2>&1)" || {
  popd >/dev/null
  cat <<EOF
{
  "ok": false,
  "workspace_path": $(json_string "$workspace_path"),
  "command_name": $(json_string "$command_name"),
  "error": $(json_string "$output")
}
EOF
  exit 1
}
popd >/dev/null

cat <<EOF
{
  "ok": true,
  "workspace_path": $(json_string "$workspace_path"),
  "command_name": $(json_string "$command_name"),
  "output": $(json_string "$output")
}
EOF
