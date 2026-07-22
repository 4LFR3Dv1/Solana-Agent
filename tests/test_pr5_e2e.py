from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from solana_agent.adapters import CounterTemplateAdapter, EvidenceAdapter
from solana_agent.execution import (
    CommandJournal,
    CommandSpec,
    ExecutionRequest,
    ExecutionResult,
    FakeExecutor,
    ValidationDecision,
)
from solana_agent.storage import Database, JournalRepository

PROGRAM_ID = "11111111111111111111111111111111"
COUNTER_PUBKEY = "SysvarRent111111111111111111111111111111111"


class EvidenceTransport:
    def call(self, endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        del endpoint, timeout_seconds
        method = payload["method"]
        if method == "getSignatureStatuses":
            return {
                "result": {
                    "value": [
                        {"confirmationStatus": "confirmed", "err": None, "slot": 1},
                        {"confirmationStatus": "finalized", "err": None, "slot": 2},
                    ]
                }
            }
        pubkey = payload["params"][0]
        if pubkey == PROGRAM_ID:
            return {"result": {"value": {"executable": True, "owner": PROGRAM_ID, "data": ["", "base64"]}}}
        raw = bytes(40) + (1).to_bytes(8, "little")
        return {
            "result": {
                "value": {
                    "executable": False,
                    "owner": PROGRAM_ID,
                    "data": [base64.b64encode(raw).decode("ascii"), "base64"],
                }
            }
        }


def request(command_id: str, adapter: str, operation: str, cwd: Path, **arguments: object) -> ExecutionRequest:
    return ExecutionRequest(command_id, adapter, operation, dict(arguments), str(cwd), 30)


def test_counter_template_materializes_program_with_generated_id(tmp_path: Path) -> None:
    workspace = tmp_path / "counter-demo"
    source = workspace / "programs" / "counter-demo" / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text(f'use anchor_lang::prelude::*;\ndeclare_id!("{PROGRAM_ID}");\n', encoding="utf-8")
    (workspace / "Anchor.toml").write_text("[provider]\ncluster = 'Localnet'\n", encoding="utf-8")
    template_root = Path(__file__).resolve().parents[1] / "templates" / "anchor-counter" / "files"
    adapter = CounterTemplateAdapter(tmp_path, template_root)

    result = adapter.execute(
        request(
            "command-template",
            "counter_template",
            "apply",
            workspace,
            workspace=str(workspace),
            project_name="counter-demo",
            cluster="devnet",
        )
    )

    assert result.exit_code == 0
    assert result.metadata["program_id"] == PROGRAM_ID
    assert result.metadata["file_count"] == 5
    assert f'declare_id!("{PROGRAM_ID}")' in source.read_text(encoding="utf-8")
    assert "PROGRAM_ID=${program.programId.toBase58()}" in (workspace / "scripts" / "interact.ts").read_text(
        encoding="utf-8"
    )


def test_evidence_adapter_verifies_rpc_state_and_exports_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "counter"
    workspace.mkdir()
    database = Database(tmp_path / "state" / "runtime.db")
    database.initialize()
    repository = JournalRepository(database)
    journal = CommandJournal(repository)
    journal.create_run(mission_id="create-counter", run_id="run-proof")
    invoke_stdout = "\n".join(
        [
            f"PROGRAM_ID={PROGRAM_ID}",
            f"COUNTER_PUBKEY={COUNTER_PUBKEY}",
            "INITIALIZE_SIGNATURE=init-signature",
            "INCREMENT_SIGNATURE=increment-signature",
            "COUNT=1",
        ]
    )
    journal.execute(
        CommandSpec("run-proof", "invoke", "package", "run", cwd=str(workspace)),
        FakeExecutor(ExecutionResult(0, stdout=invoke_stdout)),
        validator=lambda _: ValidationDecision.allow(),
    )
    evidence_spec = CommandSpec("run-proof", "evidence", "evidence", "assemble", cwd=str(workspace))
    planned = journal.plan(evidence_spec).command
    adapter = EvidenceAdapter(
        repository,
        tmp_path,
        "http://127.0.0.1:8899",
        EvidenceTransport(),
    )

    result = adapter.execute(request(planned.id, "evidence", "assemble", workspace, mission="create-counter"))

    evidence_path = Path(result.metadata["evidence_path"])
    manifest = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert result.metadata["verified"] is True
    assert manifest["verification"]["program_executable"] is True
    assert manifest["verification"]["counter_count"] == 1
    assert {item.kind for item in repository.list_artifacts(planned.id)} == {"evidence.manifest"}
