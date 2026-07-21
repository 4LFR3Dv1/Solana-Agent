from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    lock_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("toolchain.lock.json")
    payload: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    report: dict[str, Any] = {"ok": True, "tools": {}}
    for name, specification in payload["tools"].items():
        completed = subprocess.run(
            [str(item) for item in specification["command"]],
            shell=False,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        output = (completed.stdout or completed.stderr).strip().splitlines()
        version_output = output[0] if output else ""
        compatible = completed.returncode == 0 and specification["version_pattern"] in version_output
        report["tools"][name] = {
            "expected": specification["version"],
            "output": version_output,
            "compatible": compatible,
        }
        if not compatible:
            failures.append(name)
    report["ok"] = not failures
    report["failures"] = failures
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
