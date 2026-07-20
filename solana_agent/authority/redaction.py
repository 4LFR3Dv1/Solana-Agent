from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEY = re.compile(
    r"(^|_)(secret|private|seed|mnemonic|keypair|access_token|api_key|password)($|_)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (re.compile(r"\b(?:seed phrase|private key|secret key)\s*[:=]", re.IGNORECASE),)
TEXT_SECRET = re.compile(
    r"(?i)(\"?(?:private[ _]key|secret[ _]key|seed[ _]phrase|mnemonic|keypair|password)\"?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\[[^\]]*\]|[^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: dict[str, Any]
    detected_paths: tuple[str, ...]


def redact_mapping(value: dict[str, Any]) -> RedactionResult:
    detected: list[str] = []

    def visit(item: Any, path: str) -> Any:
        if isinstance(item, dict):
            output: dict[str, Any] = {}
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if SENSITIVE_KEY.search(str(key)):
                    detected.append(child_path)
                    output[str(key)] = REDACTED
                else:
                    output[str(key)] = visit(child, child_path)
            return output
        if isinstance(item, list):
            return [visit(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if isinstance(item, str) and any(pattern.search(item) for pattern in SECRET_VALUE_PATTERNS):
            detected.append(path)
            return REDACTED
        return item

    redacted = visit(value, "")
    return RedactionResult(value=redacted, detected_paths=tuple(sorted(set(detected))))


def redact_text(value: str) -> str:
    return TEXT_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
