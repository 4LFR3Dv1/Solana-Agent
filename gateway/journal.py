"""Independent durable journal for the external JSONL boundary."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from gateway.protocol import GatewayRequest


@dataclass(frozen=True)
class Reservation:
    disposition: Literal["new", "replay", "conflict", "needs_recovery"]
    sequence: int
    response: dict[str, Any] | None = None


class GatewayJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_requests (
                    gateway_request_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT,
                    state TEXT NOT NULL CHECK (
                        state IN ('reserved', 'completed', 'failed')
                    ),
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS gateway_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    gateway_request_id TEXT,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def reserve(self, request: GatewayRequest) -> Reservation:
        now = _now()
        request_json = _json(request.as_dict())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM gateway_requests WHERE gateway_request_id = ?",
                (request.gateway_request_id,),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO gateway_requests (
                        gateway_request_id, command, request_hash, request_json,
                        state, created_at
                    ) VALUES (?, ?, ?, ?, 'reserved', ?)
                    """,
                    (
                        request.gateway_request_id,
                        request.command,
                        request.request_hash,
                        request_json,
                        now,
                    ),
                )
                sequence = self._event(
                    connection,
                    request.gateway_request_id,
                    "request_reserved",
                    {"request_hash": request.request_hash, "command": request.command},
                    now,
                )
                return Reservation("new", sequence)

            if row["request_hash"] != request.request_hash:
                sequence = self._event(
                    connection,
                    request.gateway_request_id,
                    "idempotency_conflict_detected",
                    {
                        "stored_request_hash": row["request_hash"],
                        "received_request_hash": request.request_hash,
                    },
                    now,
                )
                return Reservation("conflict", sequence)

            if row["response_json"] is not None:
                response = json.loads(row["response_json"])
                response["replayed"] = True
                response = self._response_event(
                    connection,
                    request.gateway_request_id,
                    "response_replayed",
                    response,
                    now,
                )
                return Reservation("replay", response["journal_sequence"], response)

            sequence = self._event(
                connection,
                request.gateway_request_id,
                "recovery_required",
                {"state": row["state"]},
                now,
            )
            return Reservation("needs_recovery", sequence)

    def record_response(
        self,
        gateway_request_id: str,
        event_type: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._response_event(
                connection,
                gateway_request_id,
                event_type,
                response,
                _now(),
            )

    def complete(
        self,
        request: GatewayRequest,
        response: dict[str, Any],
        *,
        failed: bool,
    ) -> dict[str, Any]:
        now = _now()
        state = "failed" if failed else "completed"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            persisted = self._response_event(
                connection,
                request.gateway_request_id,
                f"request_{state}",
                response,
                now,
            )
            connection.execute(
                """
                UPDATE gateway_requests
                SET response_json = ?, state = ?, completed_at = ?
                WHERE gateway_request_id = ? AND request_hash = ?
                """,
                (
                    _json(persisted),
                    state,
                    now,
                    request.gateway_request_id,
                    request.request_hash,
                ),
            )
            return persisted

    def get(self, gateway_request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gateway_requests WHERE gateway_request_id = ?",
                (gateway_request_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        request_id: str | None,
        event_type: str,
        value: dict[str, Any],
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO gateway_events (
                gateway_request_id, event_type, event_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (request_id, event_type, _json(value), created_at),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    @classmethod
    def _response_event(
        cls,
        connection: sqlite3.Connection,
        request_id: str,
        event_type: str,
        response: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        sequence = cls._event(
            connection,
            request_id,
            event_type,
            {},
            created_at,
        )
        persisted = dict(response)
        persisted["journal_sequence"] = sequence
        connection.execute(
            "UPDATE gateway_events SET event_json = ? WHERE sequence = ?",
            (_json(persisted), sequence),
        )
        return persisted


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
