# ODIN Optimization & Backend-First Implementation Plan

Date: 2026-03-12

## Goals

1. Keep **backend as source of truth** for all indicator/trading logic.
2. Ensure support/resistance (S/R) zones are available **per-candle** in backend storage, trade evaluation, and CSV export.
3. Keep UI as a display layer only (no business logic dependency).
4. Scale ingestion and rendering safely as indicator/output count grows.
5. Preserve UI/backend contract with backward-compatible changes.

---

## Executive Decisions

- **Do not build a span engine (`from/to`) as the canonical model.**
  - Canonical model is per-candle records in backend.
  - Spans may be derived later for display/compression only.

- **Do not introduce a new indicator type.**
  - Continue using `agent_type: "indicator"` and `schema: "area"` outputs.
  - Model up to 8 zones as multiple outputs (`zone_1`...`zone_8`).

- **Backend computes confidence → color mapping.**
  - Emit numeric confidence for DSL/export and display color for chart in metadata.

---

## ACP Versioning Recommendation

## Do we need an ACP update?

**Yes, a patch update is recommended: ACP-0.4.2.**

### Why patch (0.4.2) and not major bump?

Changes are additive/backward-compatible if implemented as below:

1. Fix metadata schema output enum to include `"area"` where currently missing.
2. Clarify area metadata conventions (e.g., `confidence`, `label`, optional render hints).
3. Keep required fields unchanged and keep existing message types unchanged.

This is a non-breaking contract correction + extension, so **no major bump** is required.

### When would a major/minor breaking bump be required?

Only if we:
- Change required fields in existing records/messages,
- Change semantics incompatibly,
- Remove existing fields/types,
- Force clients to send new required payload shapes.

---

## Contract Safety Rules (Non-Negotiable)

1. **Backend authoritative:** UI never invents business values.
2. **Additive schema changes only** during these phases.
3. Existing clients continue to work if they ignore new metadata keys.
4. Any state/sync/event-stream contract changes must be reflected in:
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
- Add lightweight metrics/log counters for:
  - overlay ingest rate (records/s)
  - active overlay records per session
  - chart update latency (frontend)
  - strategy recompute latency
- Define SLO alarms (warn/fail thresholds).

### Acceptance
- Can print p50/p95 timings and counts for a live session.

---

## Phase 1 — ACP/Contract Hardening (1–2 days)

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

---

## Phase 2 — Backend Overlay Storage Correctness (2–4 days)

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

---

## Phase 3 — S/R Indicator Backend Implementation (3–5 days)

### Deliverables
1. New S/R indicator emits up to 8 area outputs.
2. Emits one record per candle per active zone (backend canonical truth).
3. Backend computes confidence + render color mapping.
4. Optional label emission.
5. Tests:
   - per-candle completeness
   - output_id partition correctness
   - confidence numeric range

### Acceptance
- CSV export includes S/R upper/lower/confidence per candle where present.
- Trade engine can reference confidence and band values without UI.

---

## Phase 4 — Trade Engine & DSL Integration (2–4 days)

### Deliverables
1. Standardize DSL variable naming for area outputs and metadata:
   - `INDICATOR:<agent>:<output_id>:upper`
   - `INDICATOR:<agent>:<output_id>:lower`
   - `INDICATOR:<agent>:<output_id>:meta:confidence`
2. Add parser/evaluator tests for S/R entry/exit logic.
3. Add backtest regression tests proving backend-only correctness.

### Acceptance
- Strategy outcomes are identical with UI disconnected.

---

## Phase 5 — Frontend Performance Optimization (Display-Only) (3–6 days)

### Principle
No business logic in UI; optimize rendering and memory only.

### Deliverables
1. Replace full-array overwrite pattern with incremental series updates where possible.
2. Add bounded in-memory windows per series (aligned with selected timeframe).
3. Batch overlay UI updates (e.g., animation frame / 100–250ms coalescing).
4. Add rendering telemetry (frame drops, update cost).

### Acceptance
- UI remains responsive at target overlay point volume and update rates.

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

## Suggested Immediate Next Steps (This Week)

1. Execute Phase 1 (ACP 0.4.2 patch + contract docs).
2. Execute Phase 2 storage key/upsert hardening.
3. Implement S/R indicator prototype for 2 zones first, then scale to 8.
4. Run load test at 1 day and 5 day windows to establish real p95 limits.

---

## Final Position

For long-term reliability and live-trading readiness:

- Keep canonical backend per-candle zone records,
- Keep UI display-only,
- Use ACP 0.4.2 patch for additive schema fixes,
- Harden backend overlay upsert semantics early,
- Then optimize frontend rendering as a separate, non-authoritative concern.
