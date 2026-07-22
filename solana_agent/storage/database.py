from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('created', 'running', 'completed', 'failed', 'cancelled', 'interrupted')
            ),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            step_id TEXT NOT NULL,
            adapter TEXT NOT NULL,
            operation TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            cwd TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds > 0),
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'planned', 'validating', 'approval_required', 'authorized', 'running',
                    'succeeded', 'failed', 'rejected', 'cancelled', 'interrupted', 'timed_out'
                )
            ),
            expected_state TEXT,
            policy_decision TEXT,
            policy_reason TEXT,
            approval_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            exit_code INTEGER,
            result_json TEXT,
            error_json TEXT,
            stdout_artifact_id TEXT,
            stderr_artifact_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            command_id TEXT REFERENCES commands(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            command_id TEXT REFERENCES commands(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            content_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_commands_run_status ON commands(run_id, status);
        CREATE INDEX IF NOT EXISTS ix_events_command ON events(command_id, sequence);
        CREATE INDEX IF NOT EXISTS ix_artifacts_command ON artifacts(command_id, kind);
        """,
    ),
    (
        2,
        """
        ALTER TABLE runs ADD COLUMN policy_snapshot_json TEXT;
        ALTER TABLE runs ADD COLUMN policy_snapshot_hash TEXT;
        ALTER TABLE commands ADD COLUMN policy_decision_id TEXT;

        CREATE TABLE policy_decisions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
            effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny', 'require_approval')),
            rule_id TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            reason TEXT NOT NULL,
            risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high', 'critical')),
            required_evidence_json TEXT NOT NULL DEFAULT '[]',
            input_snapshot_json TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
            policy_decision_id TEXT NOT NULL REFERENCES policy_decisions(id) ON DELETE CASCADE,
            manifest_json TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'approved', 'denied', 'expired', 'consumed')
            ),
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            approved_by TEXT,
            note TEXT,
            consumed_at TEXT
        );

        CREATE INDEX ix_policy_decisions_command ON policy_decisions(command_id, created_at);
        CREATE INDEX ix_approvals_command ON approvals(command_id, requested_at);
        CREATE INDEX ix_approvals_status_expiry ON approvals(status, expires_at);
        """,
    ),
    (
        3,
        """
        CREATE TABLE mission_steps (
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            step_id TEXT NOT NULL,
            definition_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'pending', 'running', 'waiting_approval', 'succeeded',
                    'failed', 'blocked', 'skipped'
                )
            ),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            command_id TEXT REFERENCES commands(id) ON DELETE SET NULL,
            result_json TEXT,
            error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id, step_id)
        );

        CREATE INDEX ix_mission_steps_run_status ON mission_steps(run_id, status);
        """,
    ),
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @classmethod
    def for_repo(cls, repo_root: Path) -> Database:
        return cls(repo_root / ".solana-agent" / "runtime.db")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(sql)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
