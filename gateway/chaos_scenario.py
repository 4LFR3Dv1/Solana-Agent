"""Drive the real JSONL gateway process through one controlled chaos scenario."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import rfc8785
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from gateway.solana_prepare import ASSOCIATED_TOKEN_PROGRAM, TOKEN_PROGRAM

SCENARIOS = {
    "kill_before_broadcast",
    "kill_after_broadcast_intent",
    "kill_during_send_transaction",
    "response_lost_after_acceptance",
    "definitive_rejection",
    "not_found_after_expiry",
    "replay_after_restart",
    "concurrent_gateways",
}


def _json_request(url: str, value: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(value, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("HTTP control response must be an object")
    return result


def _json_get(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("HTTP response must be an object")
    return result


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


class GatewayProcess:
    def __init__(
        self,
        *,
        journal: Path,
        signer: str,
        authority: str,
        proxy_endpoint: str,
        kill_point: str | None = None,
        sentinel: Path | None = None,
    ) -> None:
        arguments = [
            sys.executable,
            "-m",
            "gateway",
            "--journal",
            str(journal),
            "--signer",
            signer,
            "--foundry-authority",
            authority,
            "--rpc-endpoint",
            proxy_endpoint,
            "--allow-test-rpc-proxy",
        ]
        if kill_point is not None:
            arguments.extend(["--chaos-kill-point", kill_point])
        if sentinel is not None:
            arguments.extend(["--chaos-sentinel", str(sentinel)])
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).parents[1],
            creationflags=_creation_flags(),
        )

    def request(
        self,
        value: dict[str, Any],
        *,
        timeout: float = 15,
    ) -> dict[str, Any] | None:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("gateway pipes are unavailable")
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read() -> None:
            assert self.process.stdout is not None
            output.put(self.process.stdout.readline())

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            line = output.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("gateway response timed out") from error
        if not line:
            self.process.wait(timeout=5)
            return None
        result = json.loads(line)
        if not isinstance(result, dict):
            raise RuntimeError("gateway response must be an object")
        return result

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)


def _envelope(request_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "gateway_protocol_version": "1.0.0",
        "gateway_request_id": request_id,
        "command": command,
        "payload": payload,
    }


def _send_metrics(proxy_endpoint: str) -> dict[str, Any]:
    metrics = _json_get(proxy_endpoint.rstrip("/") + "/metrics")
    for item in metrics["methods"]:
        if item["method"] == "sendTransaction":
            return item
    return {
        "method": "sendTransaction",
        "requests_received": 0,
        "upstream_requests_forwarded": 0,
        "upstream_responses_received": 0,
        "client_responses_delivered": 0,
        "client_responses_dropped": 0,
        "rejected_before_forward": 0,
    }


def _configure_proxy(proxy_endpoint: str, method: str, mode: str, **extra: Any) -> None:
    _json_request(
        proxy_endpoint.rstrip("/") + "/control",
        {"method": method, "mode": mode, **extra},
    )


def _prepare_fixture(
    *,
    workspace: Path,
    proxy_endpoint: str,
    upstream_control: str,
    scenario: str,
) -> tuple[
    GatewayProcess,
    Keypair,
    Keypair,
    dict[str, Any],
    dict[str, Any],
    Path,
]:
    asset_signer = Keypair()
    authority = Keypair()
    destination = Pubkey.new_unique()
    mint = Pubkey.new_unique()
    token_program = Pubkey.from_string(TOKEN_PROGRAM)
    ata_program = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM)
    source_ata = Pubkey.find_program_address(
        [bytes(asset_signer.pubkey()), bytes(token_program), bytes(mint)],
        ata_program,
    )[0]
    destination_ata = Pubkey.find_program_address(
        [bytes(destination), bytes(token_program), bytes(mint)],
        ata_program,
    )[0]
    accounts = {
        str(mint): {
            "owner": TOKEN_PROGRAM,
            "data": {"parsed": {"info": {"decimals": 6}}},
        },
        str(source_ata): {
            "owner": TOKEN_PROGRAM,
            "data": {
                "parsed": {
                    "info": {
                        "mint": str(mint),
                        "owner": str(asset_signer.pubkey()),
                        "tokenAmount": {"amount": "9000000"},
                    }
                }
            },
        },
        str(destination_ata): {
            "owner": TOKEN_PROGRAM,
            "data": {
                "parsed": {
                    "info": {
                        "mint": str(mint),
                        "owner": str(destination),
                        "tokenAmount": {"amount": "0"},
                    }
                }
            },
        },
    }
    _json_request(
        upstream_control,
        {
            "accounts": accounts,
            "block_height": 90,
            "last_valid_block_height": 100,
            "signature_status_mode": "accepted",
        },
    )
    now = datetime.now(UTC).replace(microsecond=0)
    execution_request_id = f"exec_{scenario}"
    plan = {
        "protocol_version": "1.0.0",
        "normalization_profile": "foundry-pay-domain-v1",
        "obligation_id": f"obl_{scenario}",
        "network": "solana:devnet",
        "capability": "solana.spl_transfer.v1",
        "asset": {"kind": "spl-token", "mint": str(mint), "decimals": 6},
        "amount_base_units": "1000000",
        "source": str(asset_signer.pubkey()),
        "destination": str(destination),
        "expires_at": _timestamp(now + timedelta(minutes=5)),
    }
    plan_hash = _digest(plan)
    prepare_payload = {
        "request": {
            "type": "external_execution_request",
            "protocol_version": "1.0.0",
            "execution_request_id": execution_request_id,
            "idempotency_key": f"idem_{scenario}",
            "economic_plan": plan,
            "economic_plan_hash": plan_hash,
            "economic_approval": {
                "approval_id": f"approval_{scenario}",
                "economic_plan_hash": plan_hash,
                "approved_by": "chaos_operator",
                "issued_at": _timestamp(now - timedelta(seconds=1)),
                "expires_at": _timestamp(now + timedelta(minutes=4)),
            },
        },
        "preparation_context": {
            "constraints": {
                "max_fee_lamports": 50000,
                "allowed_programs": [TOKEN_PROGRAM],
            }
        },
    }
    journal = workspace / "gateway.sqlite3"
    gateway = GatewayProcess(
        journal=journal,
        signer=str(asset_signer.pubkey()),
        authority=str(authority.pubkey()),
        proxy_endpoint=proxy_endpoint,
    )
    prepared_response = gateway.request(_envelope("gw_prepare", "prepare", prepare_payload))
    if prepared_response is None or prepared_response.get("ok") is not True:
        raise RuntimeError(f"preparation failed: {prepared_response}")
    prepared = prepared_response["result"]
    authorization = {
        "type": "execution_authorization",
        "protocol_version": "1.0.0",
        "authorization_id": f"auth_{scenario}",
        "execution_request_id": execution_request_id,
        "execution_commitment_hash": prepared["execution_commitment_hash"],
        "prepared_message_hash": prepared["prepared_message_hash"],
        "signer": prepared["signer"],
        "single_use": True,
        "issued_at": _timestamp(now),
        "expires_at": _timestamp(now + timedelta(seconds=30)),
    }
    authorization["authorization_signature"] = str(authority.sign_message(rfc8785.dumps(authorization)))
    message = base64.b64decode(prepared["prepared_message_base64"], validate=True)
    message_signature = str(asset_signer.sign_message(message))
    _json_request(upstream_control, {"expected_signature": message_signature})
    execution_payload = {
        "execution_request_id": execution_request_id,
        "prepared_message_base64": prepared["prepared_message_base64"],
        "execution_authorization": authorization,
        "message_signature": {
            "signer": str(asset_signer.pubkey()),
            "signature": message_signature,
        },
    }
    return (
        gateway,
        asset_signer,
        authority,
        prepared,
        execution_payload,
        journal,
    )


def run_scenario(
    *,
    scenario: str,
    workspace: Path,
    proxy_endpoint: str,
    upstream_control: str,
    upstream_metrics: str,
) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    workspace.mkdir(parents=True, exist_ok=True)
    (
        preparation_gateway,
        asset_signer,
        authority,
        prepared,
        execution_payload,
        journal,
    ) = _prepare_fixture(
        workspace=workspace,
        proxy_endpoint=proxy_endpoint,
        upstream_control=upstream_control,
        scenario=scenario,
    )
    preparation_gateway.terminate()
    sentinel = workspace / "kill-sentinel.json"

    def gateway(kill_point: str | None = None) -> GatewayProcess:
        return GatewayProcess(
            journal=journal,
            signer=str(asset_signer.pubkey()),
            authority=str(authority.pubkey()),
            proxy_endpoint=proxy_endpoint,
            kill_point=kill_point,
            sentinel=sentinel if kill_point else None,
        )

    execute = _envelope("gw_execute", "authorize-and-execute", execution_payload)
    responses: list[dict[str, Any] | None] = []
    recovery: dict[str, Any] | None = None
    process_exit_codes: list[int] = []

    if scenario == "kill_before_broadcast":
        subject = gateway("after_execution_validated_before_claim")
        responses.append(subject.request(execute))
        process_exit_codes.append(subject.process.returncode or 0)
        subject = gateway()
        responses.append(
            subject.request(
                _envelope(
                    "gw_status",
                    "status",
                    {"execution_request_id": prepared["execution_request_id"]},
                )
            )
        )
        subject.terminate()
    elif scenario == "kill_after_broadcast_intent":
        subject = gateway("after_signature_and_broadcast_intent_persisted")
        responses.append(subject.request(execute))
        process_exit_codes.append(subject.process.returncode or 0)
        subject = gateway()
        recovery = subject.request(
            _envelope(
                "gw_recover",
                "recover",
                {"execution_request_id": prepared["execution_request_id"]},
            )
        )
        subject.terminate()
    elif scenario == "kill_during_send_transaction":
        _configure_proxy(
            proxy_endpoint,
            "sendTransaction",
            "delay_after_upstream",
            delay_ms=10000,
        )
        subject = gateway()
        result: list[dict[str, Any] | None] = []

        def invoke() -> None:
            try:
                result.append(subject.request(execute, timeout=20))
            except (TimeoutError, OSError):
                result.append(None)

        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _send_metrics(proxy_endpoint)["upstream_responses_received"] == 1:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("sendTransaction never reached upstream response")
        subject.kill()
        thread.join(timeout=2)
        responses.extend(result or [None])
        process_exit_codes.append(subject.process.returncode or 0)
        _configure_proxy(proxy_endpoint, "sendTransaction", "pass")
        subject = gateway()
        recovery = subject.request(
            _envelope(
                "gw_recover",
                "recover",
                {"execution_request_id": prepared["execution_request_id"]},
            )
        )
        subject.terminate()
    elif scenario == "response_lost_after_acceptance":
        _configure_proxy(proxy_endpoint, "sendTransaction", "drop_after_upstream")
        subject = gateway()
        responses.append(subject.request(execute))
        subject.terminate()
        _configure_proxy(proxy_endpoint, "sendTransaction", "pass")
        subject = gateway()
        recovery = subject.request(
            _envelope(
                "gw_recover",
                "recover",
                {"execution_request_id": prepared["execution_request_id"]},
            )
        )
        subject.terminate()
    elif scenario == "definitive_rejection":
        _configure_proxy(proxy_endpoint, "sendTransaction", "reject_before_forward")
        subject = gateway()
        responses.append(subject.request(execute))
        evidence = subject.request(
            _envelope(
                "gw_evidence",
                "evidence",
                {"execution_request_id": prepared["execution_request_id"]},
            )
        )
        responses.append(evidence)
        subject.terminate()
    elif scenario == "not_found_after_expiry":
        _configure_proxy(proxy_endpoint, "sendTransaction", "drop_before_forward")
        subject = gateway()
        responses.append(subject.request(execute))
        subject.terminate()
        _configure_proxy(
            proxy_endpoint,
            "getSignatureStatuses",
            "return_null_status",
        )
        _json_request(upstream_control, {"block_height": 101})
        subject = gateway()
        recovery = subject.request(
            _envelope(
                "gw_recover",
                "recover",
                {"execution_request_id": prepared["execution_request_id"]},
            )
        )
        subject.terminate()
    elif scenario == "replay_after_restart":
        _configure_proxy(proxy_endpoint, "sendTransaction", "drop_after_upstream")
        subject = gateway()
        responses.append(subject.request(execute))
        subject.terminate()
        subject = gateway()
        responses.append(subject.request(execute))
        subject.terminate()
    elif scenario == "concurrent_gateways":
        first = gateway()
        second = gateway()
        request_a = execute
        request_b = _envelope(
            "gw_execute_competing",
            "authorize-and-execute",
            execution_payload,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(first.request, request_a),
                pool.submit(second.request, request_b),
            ]
            responses.extend(future.result(timeout=20) for future in futures)
        first.terminate()
        second.terminate()

    return {
        "type": "real_process_chaos_result",
        "protocol_version": "1.0.0",
        "scenario": scenario,
        "execution_request_id": prepared["execution_request_id"],
        "signature": execution_payload["message_signature"]["signature"],
        "gateway_responses": responses,
        "recovery_response": recovery,
        "process_exit_codes": process_exit_codes,
        "kill_sentinel": (json.loads(sentinel.read_text(encoding="utf-8")) if sentinel.exists() else None),
        "proxy_send_transaction": _send_metrics(proxy_endpoint),
        "upstream_metrics": _json_get(upstream_metrics),
        "private_material_persisted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--proxy-endpoint", required=True)
    parser.add_argument("--upstream-control", required=True)
    parser.add_argument("--upstream-metrics", required=True)
    args = parser.parse_args(argv)
    result = run_scenario(
        scenario=args.scenario,
        workspace=args.workspace,
        proxy_endpoint=args.proxy_endpoint,
        upstream_control=args.upstream_control,
        upstream_metrics=args.upstream_metrics,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
