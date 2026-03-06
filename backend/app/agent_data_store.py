"""
Per-agent data container for ACP stream state.

Stores the latest bar revision per candle id and a rolling set of finalized
candles so historical backfill can merge naturally in a later phase.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class AgentDataStore:
    """Holds normalized live stream state for one agent."""

    agent_id: str
    latest_by_candle_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    finalized_bars: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5000))
    last_heartbeat_ts: str | None = None
    last_event_ts: str | None = None
    
    def update_retention(self, timeframe_days: int, interval: str) -> None:
        """Update finalized_bars maxlen based on timeframe and interval"""
        # Calculate bars needed: days * minutes_per_day / interval_minutes
        interval_minutes = self._parse_interval_to_minutes(interval)
        if interval_minutes > 0:
            # Add 10% buffer for safety
            maxlen = int((timeframe_days * 1440 / interval_minutes) * 1.1)
            # Ensure minimum of 1000 bars
            maxlen = max(maxlen, 1000)
            
            # Create new deque with updated maxlen, preserving existing data
            old_bars = list(self.finalized_bars)
            self.finalized_bars = deque(old_bars, maxlen=maxlen)

    def reset_market_data(self) -> None:
        """Clear all in-memory market data while preserving retention settings."""
        self.latest_by_candle_id.clear()
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
                return 1  # Default to 1 minute
        except ValueError:
            return 1

    def update_heartbeat(self, last_event_ts: str | None = None) -> None:
        self.last_heartbeat_ts = datetime.now(UTC).isoformat()
        if last_event_ts:
            self.last_event_ts = last_event_ts

    def ingest_ohlc(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize and upsert an OHLC record.

        - Ensures `rev` is always numeric for frontend rendering consistency.
        - Maintains latest revision per candle id.
        - Persists finalized bars in rolling storage.
        """
        candle_id = str(record.get("id", ""))
        if not candle_id:
            return record

        previous = self.latest_by_candle_id.get(candle_id)
        normalized = dict(record)

        incoming_rev = normalized.get("rev")
        if isinstance(incoming_rev, int):
            rev = incoming_rev
        elif previous and isinstance(previous.get("rev"), int):
            rev = int(previous["rev"]) + 1
        else:
            rev = 0

        normalized["rev"] = rev
        self.latest_by_candle_id[candle_id] = normalized
        self.last_event_ts = str(normalized.get("ts") or self.last_event_ts)

        if normalized.get("bar_state") == "final":
            self.finalized_bars.append(normalized)

        return normalized
