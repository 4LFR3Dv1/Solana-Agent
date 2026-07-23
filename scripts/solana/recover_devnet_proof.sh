#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:-/workspace}"
output_root="${2:-${repo_root}/.live-proof}"
program_id="${3:?program id is required}"
counter_pubkey="${4:?counter public key is required}"
deploy_signature="${5:?deploy signature is required}"
initialize_signature="${6:?initialize signature is required}"
increment_signature="${7:?increment signature is required}"
python_bin="${PYTHON_BIN:-python3}"
runtime_root="/tmp/solana-agent-recovery"
workspace="${runtime_root}/workspace"
state_root="${runtime_root}/state"
contract="${runtime_root}/runtime.devnet.json"
run_id="run-recovered-devnet-proof"
wallet="F1K3nPb4JcZ7nd6yEpWtspbCoiJzo1bL7tnUNF6SfHcp"

rm -rf "${runtime_root}"
mkdir -p "${workspace}" "${output_root}"
find "${output_root}" -mindepth 1 -depth -delete

jq -n \
  --arg wallet "${wallet}" \
  --arg workspace_root "${workspace}" \
  '{
    id: "github-actions-recovered-devnet-proof",
    version: "1.0.0",
    policy_profile: "read-only",
    workspace_root: $workspace_root,
    cluster: "devnet",
    wallet: $wallet,
    tool_versions: {solana: "4.1.2", anchor: "1.1.2", pnpm: "10.28.0"}
  }' >"${contract}"

cd "${repo_root}"
"${python_bin}" -m solana_agent --repo-root "${repo_root}" missions start verify-devnet-deploy \
  --contract "${contract}" --state-root "${state_root}" --run-id "${run_id}" \
  --input "program_id=${program_id}" --input "counter_pubkey=${counter_pubkey}" \
  --input "deploy_signature=${deploy_signature}" --input "initialize_signature=${initialize_signature}" \
  --input "increment_signature=${increment_signature}" --input expected_count=1 \
  | tee "${output_root}/independent-verification.json"

evidence="${workspace}/.solana-agent/evidence/${run_id}/evidence.json"
test -s "${evidence}"
cp "${evidence}" "${output_root}/evidence.json"
evidence_sha256="$(sha256sum "${evidence}" | awk '{print $1}')"

jq -n \
  --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg wallet "${wallet}" \
  --arg program_id "${program_id}" \
  --arg counter_pubkey "${counter_pubkey}" \
  --arg deploy_signature "${deploy_signature}" \
  --arg initialize_signature "${initialize_signature}" \
  --arg increment_signature "${increment_signature}" \
  --arg evidence_sha256 "${evidence_sha256}" \
  '{
    schema: "solana-agent-public-proof/1.0.0",
    generated_at: $generated_at,
    cluster: "devnet",
    wallet: $wallet,
    program_id: $program_id,
    counter_pubkey: $counter_pubkey,
    deploy_signature: $deploy_signature,
    initialize_signature: $initialize_signature,
    increment_signature: $increment_signature,
    expected_count: 1,
    evidence_sha256: $evidence_sha256,
    explorer: {
      program: ("https://explorer.solana.com/address/" + $program_id + "?cluster=devnet"),
      deploy: ("https://explorer.solana.com/tx/" + $deploy_signature + "?cluster=devnet"),
      initialize: ("https://explorer.solana.com/tx/" + $initialize_signature + "?cluster=devnet"),
      increment: ("https://explorer.solana.com/tx/" + $increment_signature + "?cluster=devnet")
    }
  }' >"${output_root}/public-summary.json"

jq -r '
  "Solana Agent Runtime — recovered live devnet proof",
  "PROGRAM_ID=\(.program_id)",
  "COUNTER_PUBKEY=\(.counter_pubkey)",
  "DEPLOY_SIGNATURE=\(.deploy_signature)",
  "INITIALIZE_SIGNATURE=\(.initialize_signature)",
  "INCREMENT_SIGNATURE=\(.increment_signature)",
  "EVIDENCE_SHA256=\(.evidence_sha256)",
  "INDEPENDENT_VERIFICATION=completed"
' "${output_root}/public-summary.json" | tee "${output_root}/execution-transcript.txt"
