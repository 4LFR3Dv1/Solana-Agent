from __future__ import annotations

import json
from pathlib import Path

import pytest

from solana_agent.missions import (
    MissionGraphError,
    MissionLoadError,
    load_mission,
    load_mission_pack,
    load_runtime_contract,
)
from solana_agent.missions.graph import topological_steps

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_core_pack_loads_three_declarative_missions() -> None:
    pack = load_mission_pack(REPO_ROOT / "missions")

    assert pack.id == "solana-agent-core"
    assert pack.version == "1.0.0"
    assert set(pack.missions) == {
        "create-counter",
        "deploy-existing-program",
        "verify-devnet-deploy",
    }
    assert len(pack.pack_hash) == 64


def test_runtime_contract_example_is_executable() -> None:
    contract = load_runtime_contract(REPO_ROOT / "examples" / "runtime-contract.local.json")

    assert contract.policy_profile == "local-safe"
    assert len(contract.contract_hash) == 64


def test_create_counter_has_a_valid_dependency_order() -> None:
    mission = load_mission(REPO_ROOT / "missions" / "create-counter.yaml")

    assert [step.id for step in topological_steps(mission)] == [
        "inspect-environment",
        "scaffold",
        "apply-counter-template",
        "install-dependencies",
        "build",
        "test",
        "funding-check",
        "deploy",
        "invoke",
        "evidence",
    ]


def test_json_mission_is_supported_without_code_changes(tmp_path: Path) -> None:
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "id": "custom-mission",
                "version": "1.0.0",
                "goal": "Prove dynamic mission loading.",
                "inputs": [],
                "steps": [{"id": "inspect", "adapter": "doctor", "operation": "inspect"}],
            }
        ),
        encoding="utf-8",
    )

    mission = load_mission(path)

    assert mission.id == "custom-mission"
    assert mission.steps[0].operation == "inspect"


def test_changing_mission_content_changes_its_hash(tmp_path: Path) -> None:
    path = tmp_path / "mission.yaml"
    path.write_text(
        "id: hash-test\nversion: 1.0.0\ngoal: First goal\ninputs: []\nsteps:\n  - id: inspect\n    adapter: doctor\n    operation: inspect\n",
        encoding="utf-8",
    )
    first = load_mission(path).definition_hash
    path.write_text(path.read_text(encoding="utf-8").replace("First goal", "Changed goal"), encoding="utf-8")

    second = load_mission(path).definition_hash

    assert first != second


def test_changing_a_mission_changes_the_pack_hash(tmp_path: Path) -> None:
    manifest = {"id": "hash-pack", "version": "1", "missions": ["mission.yaml"]}
    (tmp_path / "mission-pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    mission_path = tmp_path / "mission.yaml"
    mission_path.write_text(
        "id: hash-test\nversion: 1\ngoal: First\ninputs: []\nsteps:\n  - id: inspect\n    adapter: doctor\n    operation: inspect\n",
        encoding="utf-8",
    )
    first = load_mission_pack(tmp_path).pack_hash
    mission_path.write_text(mission_path.read_text(encoding="utf-8").replace("First", "Second"), encoding="utf-8")

    second = load_mission_pack(tmp_path).pack_hash

    assert first != second


def test_missing_dependency_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    path.write_text(
        "id: invalid\nversion: 1\ngoal: Invalid graph\ninputs: []\nsteps:\n  - id: build\n    adapter: anchor\n    operation: build\n    depends_on: [unknown]\n",
        encoding="utf-8",
    )

    with pytest.raises(MissionGraphError, match="unknown dependencies"):
        load_mission(path)


def test_dependency_cycle_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cycle.yaml"
    path.write_text(
        "id: cycle\nversion: 1\ngoal: Invalid graph\ninputs: []\nsteps:\n  - id: one\n    adapter: anchor\n    operation: build\n    depends_on: [two]\n  - id: two\n    adapter: anchor\n    operation: test\n    depends_on: [one]\n",
        encoding="utf-8",
    )

    with pytest.raises(MissionGraphError, match="dependency cycle"):
        load_mission(path)


def test_pack_rejects_duplicate_mission_ids(tmp_path: Path) -> None:
    mission = (
        "id: duplicate\nversion: 1\ngoal: Duplicate\ninputs: []\nsteps:\n"
        "  - id: inspect\n    adapter: doctor\n    operation: inspect\n"
    )
    (tmp_path / "one.yaml").write_text(mission, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(mission, encoding="utf-8")
    (tmp_path / "mission-pack.json").write_text(
        json.dumps({"id": "test", "version": "1", "missions": ["one.yaml", "two.yaml"]}),
        encoding="utf-8",
    )

    with pytest.raises(MissionLoadError, match="duplicate mission id"):
        load_mission_pack(tmp_path)


def test_pack_rejects_mission_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-mission.yaml"
    outside.write_text(
        "id: outside\nversion: 1\ngoal: Outside\ninputs: []\nsteps:\n  - id: inspect\n    adapter: doctor\n    operation: inspect\n",
        encoding="utf-8",
    )
    (tmp_path / "mission-pack.json").write_text(
        json.dumps({"id": "test", "version": "1", "missions": ["../outside-mission.yaml"]}),
        encoding="utf-8",
    )

    with pytest.raises(MissionLoadError, match="escapes pack root"):
        load_mission_pack(tmp_path)
