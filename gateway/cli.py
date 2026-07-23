"""Newline-delimited JSON transport. Stdout contains protocol responses only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from gateway.backend import ExternalExecutionBackend, UnavailableExecutionBackend
from gateway.journal import GatewayJournal
from gateway.protocol import GatewayError, response_envelope
from gateway.service import ExternalExecutionGateway

DEFAULT_MAX_LINE_BYTES = 1_048_576


def run_jsonl(
    input_stream: TextIO,
    output_stream: TextIO,
    gateway: ExternalExecutionGateway,
    *,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
) -> int:
    for line in input_stream:
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > max_line_bytes:
            response = _transport_error(
                GatewayError("line_too_large", "JSONL request exceeds the byte limit")
            )
        else:
            try:
                value: Any = json.loads(line, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError):
                response = _transport_error(
                    GatewayError("invalid_json", "input line is not valid JSON")
                )
            else:
                try:
                    response = gateway.handle(value)
                except GatewayError as error:
                    request_id = (
                        value.get("gateway_request_id")
                        if isinstance(value, dict)
                        else None
                    )
                    command = value.get("command") if isinstance(value, dict) else None
                    response = response_envelope(
                        request_id=request_id if isinstance(request_id, str) else None,
                        command=command if isinstance(command, str) else None,
                        ok=False,
                        error=error.as_dict(),
                    )

        output_stream.write(_json(response) + "\n")
        output_stream.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solana-agent-gateway",
        description="External execution gateway over JSONL stdin/stdout.",
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=Path(".solana-agent/gateway.sqlite3"),
        help="independent gateway SQLite journal",
    )
    parser.add_argument(
        "--max-line-bytes",
        type=int,
        default=DEFAULT_MAX_LINE_BYTES,
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    backend: ExternalExecutionBackend | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    gateway = ExternalExecutionGateway(
        GatewayJournal(args.journal),
        backend or UnavailableExecutionBackend(),
    )
    return run_jsonl(
        sys.stdin,
        sys.stdout,
        gateway,
        max_line_bytes=args.max_line_bytes,
    )


def _transport_error(error: GatewayError) -> dict[str, Any]:
    return response_envelope(
        request_id=None,
        command=None,
        ok=False,
        error=error.as_dict(),
    )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
