from __future__ import annotations

import re
from typing import Any

TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


class TemplateResolutionError(ValueError):
    pass


def resolve_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(key): resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, context) for item in value]
    if not isinstance(value, str):
        return value
    full = TEMPLATE.fullmatch(value)
    if full:
        return _lookup(context, full.group(1))

    def replace(match: re.Match[str]) -> str:
        return str(_lookup(context, match.group(1)))

    return TEMPLATE.sub(replace, value)


def _lookup(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise TemplateResolutionError(f"template value not found: {path}")
        current = current[part]
    return current
