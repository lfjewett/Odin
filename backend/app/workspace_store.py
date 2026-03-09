from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    name TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name, schema_version, created_at, updated_at
                FROM workspaces
                ORDER BY name COLLATE NOCASE ASC
                """
            ).fetchall()

        return [
            {
                "name": row["name"],
                "schema_version": int(row["schema_version"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_workspace(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT name, schema_version, state_json, created_at, updated_at
                FROM workspaces
                WHERE name = ?
                """,
                (name,),
            ).fetchone()

        if row is None:
            return None

        return {
            "name": row["name"],
            "schema_version": int(row["schema_version"]),
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_workspace(self, name: str, state: dict[str, Any], schema_version: int = 1) -> dict[str, Any]:
        existing = self.get_workspace(name)
        now = utc_now_iso()
        created_at = existing["created_at"] if existing else now

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspaces(name, schema_version, state_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (name, schema_version, json.dumps(state), created_at, now),
            )

        return self.get_workspace(name) or {
            "name": name,
            "schema_version": schema_version,
            "state": state,
            "created_at": created_at,
            "updated_at": now,
        }

    def delete_workspace(self, name: str) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM workspaces WHERE name = ?", (name,))
            deleted = result.rowcount > 0
            active = conn.execute(
                "SELECT value FROM workspace_meta WHERE key = 'active_workspace'"
            ).fetchone()
            if active and active["value"] == name:
                conn.execute("DELETE FROM workspace_meta WHERE key = 'active_workspace'")
        return deleted

    def get_active_workspace(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM workspace_meta WHERE key = 'active_workspace'"
            ).fetchone()
        return row["value"] if row else None

    def set_active_workspace(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_meta(key, value)
                VALUES('active_workspace', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value
                """,
                (name,),
            )
