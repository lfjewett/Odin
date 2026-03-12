from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GLOBAL_STRATEGY_SCOPE = "__single_user__"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeStrategyStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._has_legacy_entry_exit_columns = False
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_strategies (
                    session_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    long_entry_rule TEXT NOT NULL,
                    long_exit_rules TEXT NOT NULL DEFAULT '[]',
                    short_entry_rule TEXT NOT NULL DEFAULT '',
                    short_exit_rules TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, name)
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(trade_strategies)").fetchall()
            }
            self._has_legacy_entry_exit_columns = "entry_rule" in columns and "exit_rule" in columns

            if "long_entry_rule" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN long_entry_rule TEXT")
            if "long_exit_rules" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN long_exit_rules TEXT NOT NULL DEFAULT '[]'")
            if "short_entry_rule" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN short_entry_rule TEXT NOT NULL DEFAULT ''")
            if "short_exit_rules" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN short_exit_rules TEXT NOT NULL DEFAULT '[]'")
            if "long_entry_rules" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN long_entry_rules TEXT NOT NULL DEFAULT '[]'")
            if "short_entry_rules" not in columns:
                conn.execute("ALTER TABLE trade_strategies ADD COLUMN short_entry_rules TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def _parse_rules_json(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return []

    @staticmethod
    def _serialize_rules(rules: list[str]) -> str:
        clean = [rule.strip() for rule in rules if rule and rule.strip()]
        return json.dumps(clean)

    def _row_to_strategy(self, session_id: str, row: sqlite3.Row) -> dict[str, Any]:
        # Support both new array format (long_entry_rules) and old single-string format (long_entry_rule)
        # Prefer the new array format if available
        try:
            long_entry_rules = self._parse_rules_json(row["long_entry_rules"])
        except (IndexError, KeyError):
            long_entry_rules = []
        
        if not long_entry_rules:
            try:
                single_rule = str(row["long_entry_rule"] or "").strip()
                long_entry_rules = [single_rule] if single_rule else []
            except (IndexError, KeyError):
                long_entry_rules = []
        
        long_exit_rules = self._parse_rules_json(row["long_exit_rules"])
        long_exit_rules = [rule for rule in long_exit_rules if rule]

        # Support both new array format (short_entry_rules) and old single-string format (short_entry_rule)
        try:
            short_entry_rules = self._parse_rules_json(row["short_entry_rules"])
        except (IndexError, KeyError):
            short_entry_rules = []
        
        if not short_entry_rules:
            try:
                single_rule = str(row["short_entry_rule"] or "").strip()
                short_entry_rules = [single_rule] if single_rule else []
            except (IndexError, KeyError):
                short_entry_rules = []
        
        short_exit_rules = self._parse_rules_json(row["short_exit_rules"])

        return {
            "session_id": session_id,
            "name": row["name"],
            "description": row["description"],
            "long_entry_rules": long_entry_rules,
            "long_exit_rules": long_exit_rules,
            "short_entry_rules": short_entry_rules,
            "short_exit_rules": short_exit_rules,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _scope_key(_session_id: str) -> str:
        # Strategies are user-scoped for this single-user app.
        return GLOBAL_STRATEGY_SCOPE

    def list_strategies(self, session_id: str) -> list[dict[str, Any]]:
        scope_key = self._scope_key(session_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    session_id,
                    name,
                    description,
                    long_entry_rule,
                    long_entry_rules,
                    long_exit_rules,
                    short_entry_rule,
                    short_entry_rules,
                    short_exit_rules,
                    created_at,
                    updated_at
                FROM trade_strategies
                WHERE session_id = ?
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
                """,
                (scope_key,),
            ).fetchall()

        return [self._row_to_strategy(session_id, row) for row in rows]

    def get_strategy(self, session_id: str, name: str) -> dict[str, Any] | None:
        scope_key = self._scope_key(session_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    session_id,
                    name,
                    description,
                    long_entry_rule,
                    long_entry_rules,
                    long_exit_rules,
                    short_entry_rule,
                    short_entry_rules,
                    short_exit_rules,
                    created_at,
                    updated_at
                FROM trade_strategies
                WHERE session_id = ? AND name = ?
                """,
                (scope_key, name),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_strategy(session_id, row)

    def upsert_strategy(
        self,
        session_id: str,
        name: str,
        long_entry_rules: list[str] | None = None,
        long_exit_rules: list[str] | None = None,
        short_entry_rules: list[str] | None = None,
        short_exit_rules: list[str] | None = None,
        description: str = "",
        # Backward compatibility: accept single-string arguments
        long_entry_rule: str = "",
        short_entry_rule: str = "",
    ) -> dict[str, Any]:
        scope_key = self._scope_key(session_id)
        existing = self.get_strategy(session_id, name)
        now = utc_now_iso()
        created_at = existing["created_at"] if existing else now

        # Support both single-string (legacy) and list (new) formats
        if long_entry_rule and not long_entry_rules:
            long_entry_rules = [long_entry_rule]
        if short_entry_rule and not short_entry_rules:
            short_entry_rules = [short_entry_rule]

        normalized_long_entry_rules = [rule.strip() for rule in (long_entry_rules or []) if rule and rule.strip()]
        normalized_long_exit_rules = [rule.strip() for rule in (long_exit_rules or []) if rule and rule.strip()]
        normalized_short_entry_rules = [rule.strip() for rule in (short_entry_rules or []) if rule and rule.strip()]
        normalized_short_exit_rules = [rule.strip() for rule in (short_exit_rules or []) if rule and rule.strip()]
        
        # For legacy columns, use first entry/exit rule
        legacy_entry_rule = normalized_long_entry_rules[0] if normalized_long_entry_rules else ""
        legacy_exit_rule = normalized_long_exit_rules[0] if normalized_long_exit_rules else ""
        legacy_short_entry_rule = normalized_short_entry_rules[0] if normalized_short_entry_rules else ""

        with self._connect() as conn:
            if self._has_legacy_entry_exit_columns:
                conn.execute(
                    """
                    INSERT INTO trade_strategies(
                        session_id,
                        name,
                        description,
                        entry_rule,
                        exit_rule,
                        long_entry_rule,
                        long_entry_rules,
                        long_exit_rules,
                        short_entry_rule,
                        short_entry_rules,
                        short_exit_rules,
                        created_at,
                        updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, name) DO UPDATE SET
                        description = excluded.description,
                        entry_rule = excluded.entry_rule,
                        exit_rule = excluded.exit_rule,
                        long_entry_rule = excluded.long_entry_rule,
                        long_entry_rules = excluded.long_entry_rules,
                        long_exit_rules = excluded.long_exit_rules,
                        short_entry_rule = excluded.short_entry_rule,
                        short_entry_rules = excluded.short_entry_rules,
                        short_exit_rules = excluded.short_exit_rules,
                        updated_at = excluded.updated_at
                    """,
                    (
                        scope_key,
                        name,
                        description,
                        legacy_entry_rule,
                        legacy_exit_rule,
                        legacy_entry_rule,
                        self._serialize_rules(normalized_long_entry_rules),
                        self._serialize_rules(normalized_long_exit_rules),
                        legacy_short_entry_rule,
                        self._serialize_rules(normalized_short_entry_rules),
                        self._serialize_rules(normalized_short_exit_rules),
                        created_at,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO trade_strategies(
                        session_id,
                        name,
                        description,
                        long_entry_rule,
                        long_entry_rules,
                        long_exit_rules,
                        short_entry_rule,
                        short_entry_rules,
                        short_exit_rules,
                        created_at,
                        updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, name) DO UPDATE SET
                        description = excluded.description,
                        long_entry_rule = excluded.long_entry_rule,
                        long_entry_rules = excluded.long_entry_rules,
                        long_exit_rules = excluded.long_exit_rules,
                        short_entry_rule = excluded.short_entry_rule,
                        short_entry_rules = excluded.short_entry_rules,
                        short_exit_rules = excluded.short_exit_rules,
                        updated_at = excluded.updated_at
                    """,
                    (
                        scope_key,
                        name,
                        description,
                        legacy_entry_rule,
                        self._serialize_rules(normalized_long_entry_rules),
                        self._serialize_rules(normalized_long_exit_rules),
                        legacy_short_entry_rule,
                        self._serialize_rules(normalized_short_entry_rules),
                        self._serialize_rules(normalized_short_exit_rules),
                        created_at,
                        now,
                    ),
                )

        return self.get_strategy(session_id, name) or {
            "session_id": session_id,
            "name": name,
            "description": description,
            "long_entry_rules": normalized_long_entry_rules,
            "long_exit_rules": normalized_long_exit_rules,
            "short_entry_rules": normalized_short_entry_rules,
            "short_exit_rules": normalized_short_exit_rules,
            "created_at": created_at,
            "updated_at": now,
        }

    def delete_strategy(self, session_id: str, name: str) -> bool:
        scope_key = self._scope_key(session_id)
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM trade_strategies WHERE session_id = ? AND name = ?",
                (scope_key, name),
            )
            return result.rowcount > 0
