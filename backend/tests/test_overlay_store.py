"""
Overlay Storage Correctness Tests — Phase 2

Tests for canonical overlay identity `(agent_id, output_id, ts)` and
upsert semantics in SessionDataStore.

Acceptance criteria from OPTIMIZE.md Phase 2:
- Re-emitted/corrected overlay for same candle updates backend state deterministically.
- Different output_ids stored as independent records at the same timestamp.
- Missing output_id defaults to "default" (backward compatibility).
- Export path (get_non_ohlc_records) returns canonical latest values only.
"""

from __future__ import annotations

import pytest
from app.agent_data_store import SessionDataStore


def make_store(*, session_id: str = "session-1") -> SessionDataStore:
    return SessionDataStore(
        session_id=session_id,
        agent_id="ohlc-agent",
        symbol="SPY",
        interval="5m",
    )


SR_AGENT = "indicator-sr-agent"
TS_0 = "2026-01-05T14:30:00+00:00"
TS_1 = "2026-01-05T14:35:00+00:00"
TS_2 = "2026-01-05T14:40:00+00:00"


class TestOverlayUpsertSemantics:
    """Canonical key `(agent_id, output_id, ts)` with guaranteed upsert semantics."""

    def test_re_emit_same_candle_updates_upper_value(self):
        """Corrected record for same (output_id, ts) replaces original value — not appended."""
        store = make_store()

        store.ingest_non_ohlc(
            {"id": "r1", "ts": TS_0, "output_id": "zone_1", "upper": 100.0, "lower": 90.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )
        store.ingest_non_ohlc(
            {"id": "r2", "ts": TS_0, "output_id": "zone_1", "upper": 105.0, "lower": 92.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )

        records = store.get_non_ohlc_records()
        zone_records = [r for r in records if r.get("output_id") == "zone_1" and r.get("ts") == TS_0]

        assert len(zone_records) == 1, "Upsert must not produce duplicate records for same (output_id, ts)"
        assert zone_records[0]["upper"] == 105.0, "Latest emission's value must win"
        assert zone_records[0]["lower"] == 92.0

    def test_three_successive_corrections_final_value_wins(self):
        """After three re-emissions the third (latest) value is the only stored value."""
        store = make_store()

        for upper in [100.0, 101.0, 102.0]:
            store.ingest_non_ohlc(
                {"id": "r", "ts": TS_0, "output_id": "zone_1", "upper": upper, "lower": upper - 5},
                source_agent_id=SR_AGENT,
                schema="area",
                output_id="zone_1",
            )

        records = store.get_non_ohlc_records()
        assert len(records) == 1
        assert records[0]["upper"] == 102.0

    def test_re_emit_updates_metadata_confidence(self):
        """Re-emitting a corrected record updates metadata fields including confidence."""
        store = make_store()

        store.ingest_non_ohlc(
            {"id": "r1", "ts": TS_0, "output_id": "zone_1", "upper": 100.0, "lower": 95.0,
             "metadata": {"confidence": 0.5}},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )
        store.ingest_non_ohlc(
            {"id": "r2", "ts": TS_0, "output_id": "zone_1", "upper": 100.0, "lower": 95.0,
             "metadata": {"confidence": 0.85}},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )

        records = store.get_non_ohlc_records()
        assert len(records) == 1
        assert records[0]["metadata"]["confidence"] == 0.85


class TestOutputIdPartitioning:
    """Different output_ids at the same timestamp are stored as independent records."""

    def test_zone_1_and_zone_2_at_same_ts_stored_independently(self):
        """zone_1 and zone_2 at the same timestamp do not collide."""
        store = make_store()

        store.ingest_non_ohlc(
            {"id": "z1", "ts": TS_0, "output_id": "zone_1", "upper": 405.0, "lower": 403.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )
        store.ingest_non_ohlc(
            {"id": "z2", "ts": TS_0, "output_id": "zone_2", "upper": 395.0, "lower": 393.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_2",
        )

        records = store.get_non_ohlc_records()
        zone_map = {r["output_id"]: r for r in records if r.get("ts") == TS_0}

        assert "zone_1" in zone_map
        assert "zone_2" in zone_map
        assert zone_map["zone_1"]["upper"] == 405.0
        assert zone_map["zone_2"]["upper"] == 395.0

    def test_all_eight_zones_stored_at_same_timestamp(self):
        """All 8 S/R zone output_ids at the same timestamp produce 8 independent records."""
        store = make_store()

        for i in range(1, 9):
            store.ingest_non_ohlc(
                {
                    "id": f"z{i}",
                    "ts": TS_0,
                    "output_id": f"zone_{i}",
                    "upper": float(400 + i * 5),
                    "lower": float(400 + i * 5 - 3),
                    "metadata": {"confidence": round(0.1 * i, 1)},
                },
                source_agent_id=SR_AGENT,
                schema="area",
                output_id=f"zone_{i}",
            )

        records = store.get_non_ohlc_records()
        assert len(records) == 8
        output_ids = {r["output_id"] for r in records}
        assert output_ids == {f"zone_{i}" for i in range(1, 9)}

    def test_re_emitting_zone_1_does_not_affect_zone_2(self):
        """Upserting zone_1 leaves zone_2 record untouched."""
        store = make_store()

        store.ingest_non_ohlc(
            {"id": "z1", "ts": TS_0, "output_id": "zone_1", "upper": 405.0, "lower": 403.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )
        store.ingest_non_ohlc(
            {"id": "z2", "ts": TS_0, "output_id": "zone_2", "upper": 395.0, "lower": 393.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_2",
        )

        # Upsert zone_1 with new values
        store.ingest_non_ohlc(
            {"id": "z1b", "ts": TS_0, "output_id": "zone_1", "upper": 410.0, "lower": 408.0},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )

        records = store.get_non_ohlc_records()
        zone_map = {r["output_id"]: r for r in records}

        assert zone_map["zone_1"]["upper"] == 410.0, "zone_1 should be updated"
        assert zone_map["zone_2"]["upper"] == 395.0, "zone_2 should remain unchanged"

    def test_zone_records_across_multiple_timestamps(self):
        """Records for the same output_id at different timestamps are all stored independently."""
        store = make_store()
        timestamps = [TS_0, TS_1, TS_2]

        for i, ts in enumerate(timestamps):
            store.ingest_non_ohlc(
                {"id": f"z1-{i}", "ts": ts, "output_id": "zone_1", "upper": float(400 + i), "lower": 398.0},
                source_agent_id=SR_AGENT,
                schema="area",
                output_id="zone_1",
            )

        records = store.get_non_ohlc_records()
        zone_1_records = [r for r in records if r["output_id"] == "zone_1"]
        assert len(zone_1_records) == 3
        ts_set = {r["ts"] for r in zone_1_records}
        assert ts_set == set(timestamps)


class TestBackwardCompatibility:
    """Missing output_id falls back to 'default' — old-style agents continue working."""

    def test_missing_output_id_defaults_to_default(self):
        """A record with no output_id is stored under output_id='default'."""
        store = make_store()

        store.ingest_non_ohlc(
            {"id": "r1", "ts": TS_0, "value": 42.0},
            source_agent_id="indicator-line",
            schema="line",
        )

        records = store.get_non_ohlc_records()
        assert len(records) == 1
        assert records[0]["output_id"] == "default"
        assert records[0]["value"] == 42.0

    def test_explicit_default_output_id_same_key_as_missing(self):
        """Explicit output_id='default' and missing output_id share the same canonical key."""
        store = make_store()
        AGENT = "indicator-line"

        store.ingest_non_ohlc(
            {"id": "r1", "ts": TS_0, "value": 10.0},
            source_agent_id=AGENT,
            schema="line",
        )
        store.ingest_non_ohlc(
            {"id": "r2", "ts": TS_0, "output_id": "default", "value": 20.0},
            source_agent_id=AGENT,
            schema="line",
            output_id="default",
        )

        # Both use same canonical key — latest wins
        records = store.get_non_ohlc_records()
        assert len(records) == 1
        assert records[0]["value"] == 20.0

    def test_old_style_single_output_no_output_id_field(self):
        """Old-style agents that never set output_id still produce retrievable records."""
        store = make_store()
        AGENT = "indicator-legacy"

        for i, ts in enumerate([TS_0, TS_1]):
            store.ingest_non_ohlc(
                {"id": f"r{i}", "ts": ts, "value": float(i + 1)},
                source_agent_id=AGENT,
                schema="line",
            )

        records = store.get_non_ohlc_records()
        assert len(records) == 2
        assert all(r["output_id"] == "default" for r in records)


class TestExportPathCorrectness:
    """get_non_ohlc_records returns canonical deduplicated records for export."""

    def test_get_non_ohlc_returns_latest_after_upsert(self):
        """After upsert, export path returns the corrected value, not the original."""
        store = make_store()

        # First emission
        store.ingest_non_ohlc(
            {"id": "r1", "ts": TS_0, "output_id": "zone_1", "upper": 100.0, "lower": 95.0,
             "metadata": {"confidence": 0.5}},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )

        # Corrected emission
        store.ingest_non_ohlc(
            {"id": "r2", "ts": TS_0, "output_id": "zone_1", "upper": 110.0, "lower": 105.0,
             "metadata": {"confidence": 0.9}},
            source_agent_id=SR_AGENT,
            schema="area",
            output_id="zone_1",
        )

        records = store.get_non_ohlc_records()
        assert len(records) == 1
        assert records[0]["upper"] == 110.0
        assert records[0]["metadata"]["confidence"] == 0.9

    def test_get_non_ohlc_records_sorted_by_ts(self):
        """Export path returns records sorted by timestamp ascending."""
        store = make_store()
        timestamps = [TS_2, TS_0, TS_1]  # Deliberately out of order

        for ts in timestamps:
            store.ingest_non_ohlc(
                {"id": ts, "ts": ts, "output_id": "zone_1", "upper": 100.0, "lower": 95.0},
                source_agent_id=SR_AGENT,
                schema="area",
                output_id="zone_1",
            )

        records = store.get_non_ohlc_records()
        record_timestamps = [r["ts"] for r in records]
        assert record_timestamps == sorted(record_timestamps), "Records must be sorted by ts"

    def test_multi_zone_export_columns_are_deterministic(self):
        """All zone records appear in export output with correct output_id labels."""
        store = make_store()
        zones = {"zone_1": 405.0, "zone_2": 395.0, "zone_3": 385.0}

        for output_id, upper in zones.items():
            store.ingest_non_ohlc(
                {"id": output_id, "ts": TS_0, "output_id": output_id,
                 "upper": upper, "lower": upper - 3},
                source_agent_id=SR_AGENT,
                schema="area",
                output_id=output_id,
            )

        records = store.get_non_ohlc_records()
        export_map = {r["output_id"]: r["upper"] for r in records}

        assert export_map == zones
