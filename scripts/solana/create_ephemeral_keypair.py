from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from solders.keypair import Keypair


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: create_ephemeral_keypair.py <output.json>")
    destination = Path(sys.argv[1]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    keypair = Keypair()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(list(bytes(keypair)), stream, separators=(",", ":"))
        stream.write("\n")
    print(keypair.pubkey())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
