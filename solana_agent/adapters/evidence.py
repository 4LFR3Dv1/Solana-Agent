from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from solana_agent.execution import ExecutionRequest, ExecutionResult
from solana_agent.storage import JournalRepository

from .solana_cli import PUBLIC_KEY
from .solana_rpc import RpcTransport, UrllibRpcTransport

MARKER = re.compile(r"^(PROGRAM_ID|COUNTER_PUBKEY|INITIALIZE_SIGNATURE|INCREMENT_SIGNATURE|COUNT)=(.+)$")
DEPLOY_SIGNATURE = re.compile(r"^Signature:\s+([1-9A-HJ-NP-Za-km-z]{64,88})\s*$")
DEPLOY_PROGRAM_ID = re.compile(r"^Program Id:\s+([1-9A-HJ-NP-Za-km-z]{32,44})\s*$")


class EvidenceAdapter:
    """Verify a counter run over RPC and export a self-contained evidence manifest."""

    def __init__(
        self,
        repository: JournalRepository,
        workspace_root: Path,
        endpoint: str,
        transport: RpcTransport | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root.resolve()
        self.endpoint = endpoint
        self.transport = transport or UrllibRpcTransport()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.operation != "assemble":
            raise ValueError(f"unsupported evidence operation: {request.operation}")
        current = self.repository.require_command(request.command_id)
        workspace = self._workspace(Path(request.cwd))
        markers = self._evidence_markers(current.run_id, request.arguments)
        program_id = self._public_key(markers, "PROGRAM_ID")
        counter_pubkey = self._public_key(markers, "COUNTER_PUBKEY")
        signatures = [
            markers[name]
            for name in ("DEPLOY_SIGNATURE", "INITIALIZE_SIGNATURE", "INCREMENT_SIGNATURE")
        ]
        expected_count = int(str(request.arguments.get("expected_count", "1")))
        if int(markers["COUNT"]) != expected_count:
            raise ValueError(f"interaction reported unexpected counter value: {markers.get('COUNT')!r}")

        program = self._rpc("getAccountInfo", [program_id, {"commitment": "confirmed", "encoding": "base64"}])
        program_value = self._result_value(program, "program account")
        if program_value.get("executable") is not True:
            raise ValueError("deployed program account is not executable")

        counter = self._rpc("getAccountInfo", [counter_pubkey, {"commitment": "confirmed", "encoding": "base64"}])
        counter_value = self._result_value(counter, "counter account")
        if counter_value.get("owner") != program_id:
            raise ValueError("counter account is not owned by the deployed program")
        on_chain_count = self._counter_count(counter_value)
        if on_chain_count != expected_count:
            raise ValueError(f"unexpected on-chain counter value: {on_chain_count}")

        statuses_response = self._rpc(
            "getSignatureStatuses", [signatures, {"searchTransactionHistory": True}]
        )
        statuses = statuses_response.get("result", {}).get("value")
        if not isinstance(statuses, list) or len(statuses) != len(signatures):
            raise ValueError("RPC returned incomplete transaction statuses")
        for signature, status in zip(signatures, statuses, strict=True):
            if not isinstance(status, dict) or status.get("err") is not None:
                raise ValueError(f"transaction is not confirmed successfully: {signature}")
            if status.get("confirmationStatus") not in {"confirmed", "finalized"}:
                raise ValueError(f"transaction is not confirmed: {signature}")

        commands = self.repository.list_commands(current.run_id)
        approvals = [approval for command in commands for approval in self.repository.list_approvals(command.id)]
        manifest: dict[str, Any] = {
            "schema": "solana-agent-evidence/1.0.0",
            "run": asdict(self.repository.require_run(current.run_id)),
            "mission": str(request.arguments.get("mission", "")),
            "verification": {
                "rpc_endpoint": self.endpoint,
                "commitment": "confirmed",
                "program_id": program_id,
                "program_executable": True,
                "counter_pubkey": counter_pubkey,
                "counter_owner": counter_value.get("owner"),
                "counter_count": on_chain_count,
                "deploy_signature": signatures[0],
                "initialize_signature": signatures[1],
                "increment_signature": signatures[2],
                "signature_statuses": statuses,
            },
            "commands": [asdict(command) for command in commands],
            "approvals": [asdict(approval) for approval in approvals],
            "events": [asdict(event) for event in self.repository.list_events(current.run_id)],
        }
        encoded = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        evidence_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        destination = workspace / ".solana-agent" / "evidence" / current.run_id / "evidence.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8", newline="\n")
        artifact = self.repository.create_artifact(
            run_id=current.run_id,
            command_id=current.id,
            kind="evidence.manifest",
            content=encoded,
        )
        return ExecutionResult(
            exit_code=0,
            stdout=json.dumps(manifest["verification"], sort_keys=True, default=str),
            metadata={
                "verified": True,
                "program_id": program_id,
                "counter_pubkey": counter_pubkey,
                "count": on_chain_count,
                "signatures": signatures,
                "evidence_path": str(destination),
                "evidence_sha256": evidence_hash,
                "artifact_id": artifact.id,
            },
        )

    def _interaction_markers(self, run_id: str) -> dict[str, str]:
        invoke = next((item for item in reversed(self.repository.list_commands(run_id)) if item.step_id == "invoke"), None)
        if invoke is None or invoke.stdout_artifact_id is None:
            raise ValueError("invoke command stdout is unavailable")
        stdout = self.repository.require_artifact(invoke.stdout_artifact_id).content
        markers: dict[str, str] = {}
        for line in stdout.splitlines():
            match = MARKER.fullmatch(line.strip())
            if match:
                markers[match.group(1)] = match.group(2).strip()
        deploy = next((item for item in reversed(self.repository.list_commands(run_id)) if item.step_id == "deploy"), None)
        if deploy is not None and deploy.stdout_artifact_id is not None:
            deploy_stdout = self.repository.require_artifact(deploy.stdout_artifact_id).content
            if deploy.stderr_artifact_id is not None:
                deploy_stdout += "\n" + self.repository.require_artifact(deploy.stderr_artifact_id).content
            for line in deploy_stdout.splitlines():
                signature = DEPLOY_SIGNATURE.fullmatch(line.strip())
                if signature:
                    markers["DEPLOY_SIGNATURE"] = signature.group(1)
                deployed_program = DEPLOY_PROGRAM_ID.fullmatch(line.strip())
                if deployed_program and markers.get("PROGRAM_ID") not in {None, deployed_program.group(1)}:
                    raise ValueError("deploy and invoke reported different Program IDs")
        missing = {
            "PROGRAM_ID",
            "DEPLOY_SIGNATURE",
            "COUNTER_PUBKEY",
            "INITIALIZE_SIGNATURE",
            "INCREMENT_SIGNATURE",
            "COUNT",
        } - markers.keys()
        if missing:
            raise ValueError(f"invoke output is missing evidence markers: {sorted(missing)}")
        return markers

    def _evidence_markers(self, run_id: str, arguments: dict[str, Any]) -> dict[str, str]:
        names = {
            "PROGRAM_ID": "program_id",
            "DEPLOY_SIGNATURE": "deploy_signature",
            "COUNTER_PUBKEY": "counter_pubkey",
            "INITIALIZE_SIGNATURE": "initialize_signature",
            "INCREMENT_SIGNATURE": "increment_signature",
            "COUNT": "expected_count",
        }
        supplied = {marker: str(arguments[name]) for marker, name in names.items() if arguments.get(name) is not None}
        if supplied:
            missing = set(names) - supplied.keys()
            if missing:
                raise ValueError(f"independent verification inputs are incomplete: {sorted(missing)}")
            return supplied
        return self._interaction_markers(run_id)

    def _rpc(self, method: str, parameters: list[Any]) -> dict[str, Any]:
        response = self.transport.call(
            self.endpoint,
            {"jsonrpc": "2.0", "id": f"evidence-{method}", "method": method, "params": parameters},
            30,
        )
        if response.get("error") is not None:
            raise ValueError(f"RPC {method} failed: {response['error']}")
        return response

    @staticmethod
    def _result_value(response: dict[str, Any], label: str) -> dict[str, Any]:
        value = response.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise ValueError(f"{label} was not found on-chain")
        return value

    @staticmethod
    def _counter_count(account: dict[str, Any]) -> int:
        data = account.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], str):
            raise ValueError("counter account data is not base64 encoded")
        raw = base64.b64decode(data[0], validate=True)
        if len(raw) < 48:
            raise ValueError("counter account data is too short")
        return int.from_bytes(raw[40:48], "little")

    @staticmethod
    def _public_key(markers: dict[str, str], name: str) -> str:
        value = markers[name]
        if not PUBLIC_KEY.fullmatch(value):
            raise ValueError(f"{name} is not a valid Solana public key")
        return value

    def _workspace(self, value: Path) -> Path:
        workspace = value.resolve()
        if workspace != self.workspace_root and self.workspace_root not in workspace.parents:
            raise ValueError(f"evidence workspace escapes configured root: {workspace}")
        return workspace
