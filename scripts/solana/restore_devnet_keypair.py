from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from solders.keypair import Keypair


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: restore_devnet_keypair.py <destination>")

    encoded = os.environ.get("SOLANA_DEVNET_KEYPAIR")
    if not encoded:
        raise SystemExit("SOLANA_DEVNET_KEYPAIR is required for a persistent live proof wallet")

    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise SystemExit("SOLANA_DEVNET_KEYPAIR must contain a JSON keypair array") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 64
        or any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255 for value in payload)
    ):
        raise SystemExit("SOLANA_DEVNET_KEYPAIR must contain exactly 64 byte values")

    keypair = Keypair.from_bytes(bytes(payload))
    expected = os.environ.get("SOLANA_DEVNET_WALLET")
    if expected and str(keypair.pubkey()) != expected:
        raise SystemExit("SOLANA_DEVNET_KEYPAIR does not match SOLANA_DEVNET_WALLET")

    destination = Path(sys.argv[1]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"))
        stream.write("\n")
    print(str(keypair.pubkey()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
