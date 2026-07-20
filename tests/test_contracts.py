from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = REPO_ROOT / "contracts"


def test_all_contract_files_are_valid_json_objects() -> None:
    contract_files = sorted(CONTRACTS_ROOT.glob("*.schema.json"))

    assert contract_files
    for path in contract_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path.name


def test_all_contracts_declare_json_schema_and_required_fields() -> None:
    for path in sorted(CONTRACTS_ROOT.glob("*.schema.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("$schema") == "https://json-schema.org/draft/2020-12/schema", path.name
        assert payload.get("type") == "object", path.name
        assert isinstance(payload.get("required"), list), path.name


def test_command_contract_has_governed_lifecycle_states() -> None:
    payload = json.loads((CONTRACTS_ROOT / "command.schema.json").read_text(encoding="utf-8"))
    statuses = payload["properties"]["status"]["enum"]

    assert "planned" in statuses
    assert "authorized" in statuses
    assert "failed" in statuses
    assert "succeeded" in statuses
    assert "rejected" in statuses
