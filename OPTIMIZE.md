# ODIN Optimization & Backend-First Implementation Plan

Date: 2026-03-12

## Goals

## Phase 0 — Baseline & Guardrails ✅ COMPLETE

### Deliverables
- Add lightweight metrics/log counters for:
  - overlay ingest rate (records/s)
  - active overlay records per session
  - chart update latency (frontend)
  - strategy recompute latency
- Define SLO alarms (warn/fail thresholds).

### Acceptance
- Can print p50/p95 timings and counts for a live session.

### Completion Notes
- Backend: `_record_overlay_ingest`, `_overlay_ingest_rps`, `_active_overlay_records_by_session`,
  and `_telemetry_snapshot` implemented in `backend/app/main.py`.
- Backend: `/api/runtime/telemetry` GET endpoint returns full snapshot: counters, rps_60s,
  p50/p95/max/avg latencies, active record counts per session, session count.
- Backend: `OVERLAY_INGEST_WARN_RPS` threshold triggers `logger.warning` when exceeded.
- Frontend: ChartView overlay render timing captured in `overlayRenderLatencyMsRef` and
  logged every 5 seconds via `console.debug("[ChartView][Perf]")` with p50/p95/max/avg.

---

## Phase 1 — ACP/Contract Hardening ✅ COMPLETE
   - `docs/ui-backend-sync-contract.md`
   - `protocols/spec/ACP.md` (and `protocols/version/current.txt` when released)

---

## Canonical S/R Record Shape (Backend-emitted)

Per zone output, per candle timestamp:

- `id`: stable, unique per `(session_id, output_id, ts)`
- `ts`: candle timestamp
- `output_id`: `zone_1` ... `zone_8`
- `schema`: `area`
- `upper`: numeric
- `lower`: numeric
- `metadata.confidence`: numeric (0..1)
- `metadata.label`: optional string (e.g., `"R2"`, `"Demand"`)
- `metadata.render.primary_color`: optional display hint precomputed by backend

Notes:
- `confidence` is for DSL/export/math.
- `label` is descriptive and may be non-numeric.
- `render.*` is display hint only; trading logic never depends on it.

---

## Phased Implementation

## Phase 0 — Baseline & Guardrails (1–2 days)

### Deliverables
  - overlay ingest rate (records/s)
  - active overlay records per session
  - chart update latency (frontend)
  - strategy recompute latency

### Acceptance



### Deliverables
1. **ACP patch to 0.4.2** (backward-compatible):
   - Add `"area"` to metadata output schema enum where missing.
2. Update docs:
   - `protocols/spec/ACP.md`

### Completion Notes
   and `_telemetry_snapshot` implemented in `backend/app/main.py`.
   p50/p95/max/avg latencies, active record counts per session, session count.
   logged every 5 seconds via `console.debug("[ChartView][Perf]")` with p50/p95/max/avg.

**Status: ✅ COMPLETE**
   - `docs/ui-backend-sync-contract.md` with explicit backend-authoritative area/zone contract.

### Acceptance

## Phase 0 — Baseline & Guardrails ✅ COMPLETE

### Deliverables
- Add lightweight metrics/log counters for:
   - overlay ingest rate (records/s)
   - active overlay records per session
   - chart update latency (frontend)
   - strategy recompute latency
- Define SLO alarms (warn/fail thresholds).

### Acceptance
- Can print p50/p95 timings and counts for a live session.

### Completion Notes
- Backend: `_record_overlay_ingest`, `_overlay_ingest_rps`, `_active_overlay_records_by_session`,
   and `_telemetry_snapshot` implemented in `backend/app/main.py`.
- Backend: `/api/runtime/telemetry` GET endpoint returns full snapshot: counters, rps_60s,
   p50/p95/max/avg latencies, active record counts per session, session count.
- Backend: `OVERLAY_INGEST_WARN_RPS` threshold triggers `logger.warning` when exceeded.
- Frontend: ChartView overlay render timing captured in `overlayRenderLatencyMsRef` and
   logged every 5 seconds via `console.debug("[ChartView][Perf]")` with p50/p95/max/avg.

---

## Phase 1 — ACP/Contract Hardening ✅ COMPLETE

### Deliverables
1. **ACP patch to 0.4.2** (backward-compatible):
    - Add `"area"` to metadata output schema enum where missing.
    - Document recommended area metadata keys (`confidence`, `label`, `render.primary_color`).
2. Update docs:
    - `protocols/spec/ACP.md`
    - `protocols/docs/ACP-0.4.1-CHANGES.md` (or add 0.4.2 changes doc)
    - `docs/ui-backend-sync-contract.md` with explicit backend-authoritative area/zone contract.

### Acceptance
- Existing indicators still run unchanged.
- New S/R indicator metadata is valid under schema.

### Completion Notes
- ACP-0.4.2 changes documented in `protocols/docs/ACP-0.4.2-CHANGES.md`.
- ACP-0.4.3 subsequently released (`protocols/docs/ACP-0.4.3-CHANGES.md`).
- `docs/ui-backend-sync-contract.md` updated with backend-authoritative area-zone rules.

---

## Phase 2 — Backend Overlay Storage Correctness ✅ COMPLETE

### Problem addressed
Current non-OHLC dedup is first-write-wins by `(agent_id, record_id)`.
Long term, overlays need deterministic upsert behavior for corrections and stable per-candle state.

### Deliverables
1. Normalize overlay identity to canonical key:
   - `(agent_id, output_id, ts)` (+ optional `rev` if present)
2. Upsert semantics:
   - latest value replaces prior value for same canonical key.
3. Keep backward compatibility:
   - if `output_id` missing, treat as `default`.
4. Ensure export path reads canonical latest overlays.

### Acceptance
- Re-emitted/corrected overlay for same candle updates backend state and export output deterministically.

### Completion Notes
- `agent_data_store.py` `ingest_non_ohlc` uses `(agent_id, output_id::ts)` as canonical dedup key.
- `get_non_ohlc_records()` returns deduplicated latest values sorted by timestamp.
- Backward compat: missing `output_id` normalizes to `"default"`.
- Tests: `backend/tests/test_overlay_store.py` (upsert semantics, partition, backward compat, export).

---

## Phase 3 — S/R Indicator Backend Implementation ✅ COMPLETE

### Deliverables
1. New S/R indicator emits up to 8 area outputs.
2. Emits one record per candle per active zone (backend canonical truth).
3. Indicator computes confidence + render color mapping.
4. Optional label emission.
5. Tests:
   - per-candle completeness
   - output_id partition correctness
   - confidence numeric range

### Acceptance
- CSV export includes S/R upper/lower/confidence per candle where present.
- Trade engine can reference confidence and band values without UI.

### Completion Notes
- S/R indicator agent connected, tested, and all UI elements verified working.
- Emits `schema: area`, `output_id: zone_1..zone_N`, `metadata.confidence` (0..1), `metadata.render.primary_color`.
- Backend stores all zones via canonical `ingest_non_ohlc` with per-candle identity.
- Tests: `TestSRIndicatorProperties` in `backend/tests/test_dsl.py` covers all acceptance criteria.

---

## Phase 4 — Trade Engine & DSL Integration ✅ COMPLETE

### Deliverables
1. Standardize DSL variable naming for area outputs and metadata:
   - `{agent}:{output_id}:upper` (canonical, e.g. `SR:zone_1:upper`)
   - `{agent}:{output_id}:lower` (canonical, e.g. `SR:zone_1:lower`)
   - `{agent}:{output_id}:{metadata_key}` (canonical, e.g. `SR:zone_1:confidence`)
2. Add parser/evaluator tests for S/R entry/exit logic.
3. Add backtest regression tests proving backend-only correctness.

### Acceptance
- Strategy outcomes are identical with UI disconnected.

### Completion Notes
- `build_area_field_variable_name` / `build_area_metadata_variable_name` in `models.py` produce canonical names.
- `_build_indicator_variable_maps` in `trade_engine.py` resolves upper/lower/confidence per candle.
- Legacy names (`{agent}:{label}:meta_{key}`) also registered for backward compat.
- Tests: `TestSRIndicatorDSLVariableNames` and `TestSRIndicatorDSLEvaluation` in `backend/tests/test_dsl.py`.
  - Confidence-gated entry/exit strategies evaluated end-to-end against real trade engine.
  - Upserted overlays reflected correctly in DSL variable resolution.

---

## Phase 5 — Frontend Performance Optimization (Display-Only) ✅ COMPLETE

### Principle
No business logic in UI; optimize rendering and memory only.

### Deliverables
1. Replace full-array overwrite pattern with incremental series updates where possible.
2. Add bounded in-memory windows per series (aligned with selected timeframe).
3. Batch overlay UI updates (e.g., animation frame / 100–250ms coalescing).
4. Add rendering telemetry (frame drops, update cost).

### Acceptance
- UI remains responsive at target overlay point volume and update rates.

### Completion Notes
- **Phase 5a — Overlay Batching** (`App.tsx`): `handleOverlay` no longer calls `setOverlayData`
   directly. Instead, it accumulates events into `pendingLiveOverlayRef` (a `Map<seriesKey, { records, schema }>`).
   A `flushLiveOverlayBatch` callback runs every 150ms via `window.setInterval`, collapsing all
   queued events into a single `setOverlayData` + `setOverlaySchemas` call — reducing ChartView
   re-renders from one-per-event down to ~7/second maximum.
- **Phase 5b — Render Telemetry** (`ChartView.tsx`): Already implemented. `overlayRenderLatencyMsRef`
   accumulates per-render timings (up to 240 samples) and emits p50/p95/max/avg every 5 seconds
   via `console.debug("[ChartView][Perf]")`. Backend telemetry at `/api/runtime/telemetry`.
- **Phase 5c — Bounded Overlay Window** (`App.tsx`): `flushLiveOverlayBatch` trims each series
   array to `max(500, selectedTimeframe × 1440)` records after applying the batch. This bounds
   overlay memory growth for long live-trading sessions. A 7-day window caps at ~10,080 records/series.
- **Phase 5d — Incremental Series Update** (`ChartView.tsx`): Line series rendering now uses
   `series.update(lastPoint)` (O(1)) instead of `series.setData(allData)` (O(n)) when exactly one
   new point was appended at the end. Falls back to `setData` for history loads, reorders, or
   out-of-window data. `lineSeriesLastPointRef` tracks per-series `{length, lastTime}` for detection.
   Cleanup integrated at all series-removal sites (main loop, pane change, area replacement).

**Status: ✅ COMPLETE**

---

## Scalability Scope & Practical Limits

Your current responsive state (11 variables, 4 agents, 1s updates) is expected.

### Useful sizing formula

`points_per_day_per_output ≈ 23,400` (US RTH seconds)

`total_points ≈ outputs × points_per_day_per_output × days_loaded`

Examples (1 day loaded):
- 11 outputs ≈ 257k points
- 20 outputs ≈ 468k points
- 50 outputs ≈ 1.17M points

### Practical guidance (current architecture)

With current full-map/full-series processing patterns, expect:
- **Comfortable:** up to ~100k active overlay points
- **Caution:** ~150k–300k (intermittent jank likely)
- **High risk:** >300k–500k (noticeable lag likely)

These are hardware-dependent, but good planning ranges.

### Agent-count answer (what you asked directly)

There is no hard limit by agent count alone. The limit is effectively:

- total active outputs,
- update frequency,
- retained points per output,
- and whether updates are incremental vs full recompute.

So “6 vs 10 vs 20 vs 50 agents” depends on outputs/agent and retention window.
A 20-agent setup can outperform a 6-agent setup if outputs and retained points are lower.

---

## Migration Safety Checklist

Before each phase release:

1. Backward compatibility tests pass for existing indicators.
2. `docs/ui-backend-sync-contract.md` updated for any sync/event/subscription behavior changes.
3. Export regression test verifies overlay columns + metadata columns.
4. Trade strategy regression tests pass without frontend involvement.
5. Replay/resync tests confirm no data loss under reconnect.

---

## Suggested Immediate Next Steps (Current)

Phases 1–4 are complete. Remaining work:

**All phases complete.** Recommended next actions:

1. **Run load tests** — Execute a 1-day and 5-day live-window session and pull
   `/api/runtime/telemetry` snapshots. Check `console.debug("[ChartView][Perf]")` logs for
   p95 overlay render latency. Compare against comfortable range (<100k active overlay points).
2. **Tune the flush interval** — The 150ms batch window in `App.tsx` can be reduced to 100ms
   or increased to 250ms based on p95 measurements from live sessions.
3. **Extend Phase 5d to area series** — Currently only line series use incremental `update()`.
   Area series still call `setData()` on both `upperSeries` and `lowerSeries`. If area-heavy
   workloads show high render latency, apply the same incremental pattern there.
4. **Phase 0 SLO alerts surfaced in UI** — Consider a small toast/badge when
   `/api/runtime/telemetry` overlay `rps_60s` exceeds `warn_threshold_rps`.

---

## Final Position

For long-term reliability and live-trading readiness:

- Keep canonical backend per-candle zone records,
- Keep UI display-only,
- Use ACP 0.4.2 patch for additive schema fixes,
- Harden backend overlay upsert semantics early,
- Then optimize frontend rendering as a separate, non-authoritative concern.
