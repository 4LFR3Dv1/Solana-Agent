#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:-/workspace}"
output_root="${2:-${repo_root}/.live-proof}"
runtime_root="/tmp/solana-agent-live"
workspace="${runtime_root}/workspaces/counter-proof"
state_root="${runtime_root}/state"
contract="${runtime_root}/runtime.devnet.json"
wallet_path="${HOME}/.config/solana/id.json"
transcript="${output_root}/execution-transcript.txt"

rm -rf "${runtime_root}" "${output_root}"
mkdir -p "$(dirname "${wallet_path}")" "${runtime_root}/workspaces" "${output_root}"
solana-keygen new --no-bip39-passphrase --silent --force --outfile "${wallet_path}" >/dev/null
wallet="$(solana address --keypair "${wallet_path}")"
solana config set --url devnet --keypair "${wallet_path}" >/dev/null
export ANCHOR_PROVIDER_URL="https://api.devnet.solana.com"
export ANCHOR_WALLET="${wallet_path}"

jq -n \
  --arg wallet "${wallet}" \
  --arg workspace_root "${runtime_root}/workspaces" \
  '{
    id: "github-actions-live-devnet-proof",
    version: "1.0.0",
    policy_profile: "devnet-safe",
    workspace_root: $workspace_root,
    cluster: "devnet",
    wallet: $wallet,
    max_lamports: 2000000000,
    tool_versions: {solana: "4.1.2", anchor: "1.1.2", pnpm: "10.28.0"}
  }' >"${contract}"

cd "${repo_root}"
{
  echo "Solana Agent Runtime — governed live devnet proof"
  echo "UTC_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "CLUSTER=devnet"
  echo "WALLET=${wallet}"
} | tee "${transcript}"

set +e
result="$(python3 -m solana_agent --repo-root "${repo_root}" missions start create-counter \
  --contract "${contract}" --state-root "${state_root}" --run-id run-live-devnet-proof \
  --input "workspace=${workspace}" --input project_name=counter-proof --input airdrop_amount=2 2>&1)"
exit_code=$?
set -e
printf '%s\n' "${result}" | tee -a "${transcript}"
run_id="$(printf '%s' "${result}" | jq -r '.run_id // empty' 2>/dev/null || true)"
if [[ -z "${run_id}" ]]; then
  echo "mission did not return a run id (exit ${exit_code})" >&2
  exit 1
fi

for attempt in $(seq 1 16); do
  status="$(printf '%s' "${result}" | jq -r '.status // "unknown"' 2>/dev/null || true)"
  echo "MISSION_STATUS=${status} LOOP=${attempt}" | tee -a "${transcript}"
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  approvals="$(python3 -m solana_agent --repo-root "${repo_root}" approvals list "${run_id}" \
    --contract "${contract}" --state-root "${state_root}")"
  approval_id="$(printf '%s' "${approvals}" | jq -r '[.approvals[] | select(.status == "pending")] | last | .id // empty')"
  if [[ -n "${approval_id}" ]]; then
    echo "APPROVING=${approval_id}" | tee -a "${transcript}"
    python3 -m solana_agent --repo-root "${repo_root}" approvals approve "${approval_id}" \
      --by github-actions-live-proof --note "User-authorized PR5 devnet proof" \
      --contract "${contract}" --state-root "${state_root}" | tee -a "${transcript}"
  fi
  set +e
  result="$(python3 -m solana_agent --repo-root "${repo_root}" missions resume "${run_id}" \
    --contract "${contract}" --state-root "${state_root}" 2>&1)"
  exit_code=$?
  set -e
  printf '%s\n' "${result}" | tee -a "${transcript}"
  if [[ ${attempt} -eq 16 ]]; then
    echo "mission did not complete after ${attempt} resume cycles (last exit ${exit_code})" >&2
    exit 1
  fi
done

evidence="${workspace}/.solana-agent/evidence/${run_id}/evidence.json"
test -s "${evidence}"
cp "${evidence}" "${output_root}/evidence.json"
evidence_sha256="$(sha256sum "${evidence}" | awk '{print $1}')"

program_id="$(jq -r '.verification.program_id' "${evidence}")"
counter_pubkey="$(jq -r '.verification.counter_pubkey' "${evidence}")"
deploy_signature="$(jq -r '.verification.deploy_signature' "${evidence}")"
initialize_signature="$(jq -r '.verification.initialize_signature' "${evidence}")"
increment_signature="$(jq -r '.verification.increment_signature' "${evidence}")"

python3 -m solana_agent --repo-root "${repo_root}" missions start verify-devnet-deploy \
  --contract "${contract}" --state-root "${runtime_root}/verify-state" --run-id run-independent-verification \
  --input "program_id=${program_id}" --input "counter_pubkey=${counter_pubkey}" \
  --input "deploy_signature=${deploy_signature}" --input "initialize_signature=${initialize_signature}" \
  --input "increment_signature=${increment_signature}" --input expected_count=1 \
  | tee "${output_root}/independent-verification.json"

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

{
  echo "UTC_END=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "PROGRAM_ID=${program_id}"
  echo "COUNTER_PUBKEY=${counter_pubkey}"
  echo "DEPLOY_SIGNATURE=${deploy_signature}"
  echo "INITIALIZE_SIGNATURE=${initialize_signature}"
  echo "INCREMENT_SIGNATURE=${increment_signature}"
  echo "EVIDENCE_SHA256=${evidence_sha256}"
  echo "INDEPENDENT_VERIFICATION=completed"
} | tee -a "${transcript}"

# The ephemeral signer is intentionally destroyed with /tmp when the container exits.
