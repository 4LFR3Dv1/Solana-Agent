#!/usr/bin/env bash
set -euo pipefail

TARGET_HOME="${1:-/home/foundry}"

if [[ ! -d "$TARGET_HOME" ]]; then
  echo "Target home does not exist: $TARGET_HOME" >&2
  exit 1
fi

PROFILE="$TARGET_HOME/.profile"
BASHRC="$TARGET_HOME/.bashrc"

sed -i 's/\r$//' "$PROFILE" "$BASHRC"
sed -i '\|/root/.cargo/env|d' "$PROFILE" "$BASHRC"
sed -i '\|/root/.local/share/solana|d' "$PROFILE" "$BASHRC"
sed -i '\|/root/.cargo/bin|d' "$PROFILE" "$BASHRC"
sed -i '\|/root/.avm|d' "$PROFILE" "$BASHRC"
sed -i '\|/root/.nvm|d' "$PROFILE" "$BASHRC"

for line in \
  '. "$HOME/.cargo/env"' \
  'export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"' \
  'export NVM_DIR="$HOME/.nvm"' \
  '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' \
  '[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"' \
  'export AVM_HOME="$HOME/.avm"' \
  'export PATH="$AVM_HOME/bin:$PATH"' \
  'export PATH="$HOME/.local/bin:$PATH"'
do
  grep -qxF "$line" "$PROFILE" || printf '\n%s\n' "$line" >> "$PROFILE"
done

chown -R foundry:foundry \
  "$TARGET_HOME/.cargo" \
  "$TARGET_HOME/.rustup" \
  "$TARGET_HOME/.local" \
  "$TARGET_HOME/.nvm" \
  "$TARGET_HOME/.avm"

ln -sf "$TARGET_HOME/.avm/bin/avm" "$TARGET_HOME/.avm/bin/anchor"
ln -sf "$TARGET_HOME/.avm/bin/avm" "$TARGET_HOME/.cargo/bin/anchor"

echo "Updated $TARGET_HOME environment."
