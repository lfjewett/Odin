"""
Per-session canonical data container for ACP v0.4.x.

Stores canonical OHLC records with proper deduplication (by agent_id, id, rev
for OHLC; by agent_id plus canonical overlay identity for non-OHLC). Maintains
rolling window of finalized candles for historical queries.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionDataStore:
    """
    Holds canonical market data for a single session.
    
    Per ACP v0.4.x:
    - OHLC deduplication: (agent_id, id, rev) — upsert on higher rev
    - Non-OHLC canonical identity: (agent_id, output_id, ts|id) with upsert semantics
    """

    session_id: str
    agent_id: str
    symbol: str
    interval: str
    
    # Canonical storage: id -> {latest record}
    latest_by_candle_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    
    # Revision tracking: (agent_id, id) -> latest_rev for dedup
    latest_rev_by_ohlc: dict[tuple[str, str], int] = field(default_factory=dict)
    
    # Finalized bars rolling buffer
    finalized_bars: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5000))

    # Non-OHLC storage and dedup tracking:
    # key: (source_agent_id, canonical_overlay_key) -> latest record
    latest_non_ohlc_by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    
    # Activity timestamps
    last_heartbeat_ts: str | None = None
    last_event_ts: str | None = None

    def __post_init__(self):
        """Initialize retention based on standard timeframe"""
        self.update_retention(timeframe_days=30, interval=self.interval)

    def update_retention(self, timeframe_days: int, interval: str) -> None:
        """Update finalized_bars maxlen based on timeframe and interval"""
        interval_minutes = self._parse_interval_to_minutes(interval)
        if interval_minutes > 0:
            # Calculate bars needed: days * minutes_per_day / interval_minutes
            maxlen = int((timeframe_days * 1440 / interval_minutes) * 1.1)
            maxlen = max(maxlen, 1000)  # Ensure minimum of 1000 bars
            
            # Recreate deque with new maxlen, preserving existing data
            old_bars = list(self.finalized_bars)
            self.finalized_bars = deque(old_bars, maxlen=maxlen)

    def reset_market_data(self) -> None:
        """Clear all in-memory market data while preserving retention settings."""
        self.latest_by_candle_id.clear()
        self.latest_rev_by_ohlc.clear()
        self.latest_non_ohlc_by_key.clear()
        self.finalized_bars.clear()
        self.last_event_ts = None

    @staticmethod
    def _parse_interval_to_minutes(interval: str) -> int:
        """Convert interval string (1m, 5m, 1h, 1d) to minutes"""
        interval = interval.lower().strip()
        try:
            if interval.endswith('m'):
                return int(interval[:-1])
            elif interval.endswith('h'):
                return int(interval[:-1]) * 60
            elif interval.endswith('d'):
                return int(interval[:-1]) * 1440
            else:
                return 1
        except ValueError:
            return 1

    def ingest_ohlc(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """
        Normalize and ingest an OHLC record with deduplication.
        
        ACP v0.2.0 deduplication: (agent_id, id, rev)
        - Upserts record if rev is higher than previously seen for this (agent_id, id)
        - Returns the normalized record if ingested, None if rejected (duplicate/lower rev)
        """
        candle_id = str(record.get("id", ""))
        if not candle_id:
            return None

        # Normalize rev to integer
        incoming_rev = record.get("rev", 0)
        if isinstance(incoming_rev, str):
            try:
                incoming_rev = int(incoming_rev)
            except (ValueError, TypeError):
                incoming_rev = 0
        elif not isinstance(incoming_rev, int):
            incoming_rev = 0

        # Deduplication key for OHLC
        dedup_key = (self.agent_id, candle_id)
        latest_rev = self.latest_rev_by_ohlc.get(dedup_key, -1)

        # Reject if we've seen this or a higher rev
        if incoming_rev <= latest_rev:
            return None  # Duplicate or lower rev, skip

        # Ingest: upsert canonical record and update rev tracker
        normalized = dict(record)
        normalized["rev"] = incoming_rev
        self.latest_by_candle_id[candle_id] = normalized
        self.latest_rev_by_ohlc[dedup_key] = incoming_rev
        self.last_event_ts = str(normalized.get("ts") or self.last_event_ts)

        # Track finalized bars
        if normalized.get("bar_state") == "final":
            self.finalized_bars.append(normalized)

        return normalized

    def ingest_non_ohlc(
        self,
        record: dict[str, Any],
        source_agent_id: str | None = None,
        schema: str | None = None,
        subscription_id: str | None = None,
        output_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Ingest a non-OHLC record (e.g., area, event, line) with canonical upsert semantics.

        Canonical identity prefers `(output_id, ts)` so multi-output indicators can
        emit records with the same `id` without collapsing distinct outputs.
        """
        raw_record_id = str(record.get("id", "")).strip()
        record_ts = str(record.get("ts", "")).strip()
        normalized_output_id = str(output_id or record.get("output_id") or "default").strip() or "default"

        if not raw_record_id and not record_ts:
            return None

        dedup_agent_id = source_agent_id or self.agent_id
        canonical_record_key = f"{normalized_output_id}::{record_ts or raw_record_id}"
        dedup_key = (dedup_agent_id, canonical_record_key)

        normalized = dict(record)
        if raw_record_id:
            normalized["id"] = raw_record_id
        if source_agent_id and not normalized.get("agent_id"):
            normalized["agent_id"] = source_agent_id
        if schema and not normalized.get("schema"):
            normalized["schema"] = schema
        if subscription_id and not normalized.get("subscription_id"):
            normalized["subscription_id"] = subscription_id
        normalized["output_id"] = normalized_output_id
        self.latest_non_ohlc_by_key[dedup_key] = normalized
        self.last_event_ts = str(normalized.get("ts") or self.last_event_ts)

        return normalized

    def update_heartbeat(self, last_event_ts: str | None = None) -> None:
        """Update heartbeat and optional event timestamp"""
        self.last_heartbeat_ts = datetime.now(timezone.utc).isoformat()
        if last_event_ts:
            self.last_event_ts = last_event_ts

    def get_canonical_candles(self) -> list[dict[str, Any]]:
        """Get all canonical candles, sorted by id"""
        return sorted(self.latest_by_candle_id.values(), key=lambda x: x.get("id", ""))

    def get_finalized_candles(self) -> list[dict[str, Any]]:
        """Get finalized candles in order"""
        return list(self.finalized_bars)

    def get_non_ohlc_records(self) -> list[dict[str, Any]]:
        """Get canonical non-OHLC records sorted by timestamp then id."""
        enriched: list[dict[str, Any]] = []
        for (source_agent_id, _record_id), record in self.latest_non_ohlc_by_key.items():
            normalized = dict(record)
            if source_agent_id and not normalized.get("agent_id"):
                normalized["agent_id"] = source_agent_id
            enriched.append(normalized)

        return sorted(
            enriched,
            key=lambda record: (
                str(record.get("ts") or ""),
                str(record.get("id") or ""),
            ),
        )
