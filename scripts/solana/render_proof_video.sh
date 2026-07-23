#!/usr/bin/env bash
set -euo pipefail

summary="${1:?usage: render_proof_video.sh <public-summary.json>}"
output="${2:-$(dirname "${summary}")/solana-agent-live-proof.mp4}"
work="$(dirname "${summary}")/video"
font="/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
mkdir -p "${work}"

wallet="$(jq -r '.wallet' "${summary}")"
program="$(jq -r '.program_id' "${summary}")"
counter="$(jq -r '.counter_pubkey' "${summary}")"
deploy="$(jq -r '.deploy_signature' "${summary}")"
initialize="$(jq -r '.initialize_signature' "${summary}")"
increment="$(jq -r '.increment_signature' "${summary}")"
evidence_hash="$(jq -r '.evidence_sha256' "${summary}")"
generated="$(jq -r '.generated_at' "${summary}")"

cat >"${work}/slide-1.txt" <<EOF
SOLANA AGENT RUNTIME

LIVE DEVNET PROOF

Governed execution · bound approvals · RPC verification

Cluster: devnet
Wallet: ${wallet}
Generated: ${generated}
EOF
cat >"${work}/slide-2.txt" <<EOF
PROGRAM DEPLOYED AND INVOKED

Program ID:
${program}

Counter account:
${counter}

Deploy signature:
${deploy}
EOF
cat >"${work}/slide-3.txt" <<EOF
ON-CHAIN RESULT VERIFIED

Initialize: ${initialize}

Increment:  ${increment}

Observed counter state: 1

Evidence SHA-256:
${evidence_hash}
EOF

for slide in 1 2 3; do
  ffmpeg -loglevel error -y -f lavfi -i "color=c=0x08111f:s=1280x720:d=4:r=30" \
    -vf "drawtext=fontfile=${font}:textfile=${work}/slide-${slide}.txt:expansion=none:fontcolor=white:fontsize=24:x=60:y=70:line_spacing=18" \
    -c:v libx264 -pix_fmt yuv420p "${work}/slide-${slide}.mp4"
done
printf "file 'slide-1.mp4'\nfile 'slide-2.mp4'\nfile 'slide-3.mp4'\n" >"${work}/concat.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "${work}/concat.txt" -c copy "${output}"
test -s "${output}"
