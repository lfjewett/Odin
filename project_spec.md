# Project Specification: Phased Trading UI Platform

## 1) Purpose
Build a TradingView-style platform that unifies real-time market candles (OHLCV) and AI/agent overlay events on a single timeline. The delivery strategy is phased: start with an extremely small MVP, then add features incrementally while preserving reliability and extensibility.

## 2) Product Scope (Current)
- Single-user product initially.
- Read-only recommendations in MVP (no order execution).
- ACP test price agent feed first (local dev at `http://localhost:8010`).
- At-least-once event delivery with deterministic deduplication.
- Single backend application (Python + FastAPI) that serves both:
  - WebSocket streams for real-time market and overlay data
  - Static React assets (built from TypeScript source)
- Local Docker development plus CI build/test automation.

## 3) Confirmed Technical Decisions
- **Single Application:** Python + FastAPI backend serving static React assets
  - Backend handles all server logic, WebSocket streams, and REST APIs
  - Frontend is React + TypeScript, built as static HTML/JS/CSS and served by backend
  - Browser client connects via WebSocket for real-time data, REST for history
- **Charting:** TradingView Lightweight Charts (React wrapper).
- **Backend Stack:** Python + FastAPI + asyncio
  - WebSocket server for streaming market and overlay data
  - REST endpoints for historical data and user config
  - Static file serving for frontend assets
- **Stream reliability model:** at-least-once + dedupe using `event_id`.
- **MVP persistence boundary:**
  - Persist user configuration in SQLite.
  - Keep market stream buffering in-memory in MVP.
- **Future durability path:**
  - Move market persistence to Postgres/Timescale when replay/history demands increase.

## 4) Non-Goals (for MVP)
- No multi-user auth/tenant model.
- No live order execution.
- No durable market data warehouse in MVP.
- No full cloud deployment hardening in MVP.

## 5) Architecture Contract Requirements
Before implementation, lock and document a canonical event contract:
- `event_id` (global unique identifier for dedupe/replay)
- `trace_id` (cross-system observability)
- `symbol`
- `ts_event` (producer event time)
- `ts_ingest` (backend ingest time)
- message type (price/overlay/system)
- payload schema version
- ordering/replay semantics

Supporting docs:
- `architecture.md`
- `docs/adr/0001-frontend-react.md`
- `docs/adr/0002-delivery-semantics.md`
- `docs/protocol/event-schema.md`
- `docs/protocol/ws.md`

## 6) User Configuration Persistence Strategy
Use SQLite for MVP user configuration persistence because it is simple, reliable for single-user operation, and extensible through migrations.

### 6.1 Persisted entities
- Agent subscriptions
- Agent configurations
- Indicator configurations
- Chart presets/layout
- User preferences

### 6.2 Data access behavior
- Upsert-based writes for idempotent saves.
- Optimistic concurrency via a version field.
- Bootstrap on startup: load config and hydrate frontend state.
- Add JSON export/import endpoint for portability and recovery.
- Keep periodic backup of the SQLite file.

### 6.3 Storage evolution
- Keep schema versioned from day one.
- Introduce migration tooling immediately.
- Migrate SQLite-backed config to relational production storage during Phase 5 when broader persistence is introduced.

## 7) Phased Development Plan

### Phase 0: Protocol + Hello Stream
Goal: establish end-to-end stream with minimal UI and strict contracts.

Deliverables:
- Simulated tick/candle generator.
- Backend WebSocket fanout.
- Static React build serving minimal chart shell (single symbol/timeframe).
- Protocol docs and ADRs finalized.
- Backend to serve static React frontend on startup.

Suggested implementation targets:
- `backend/app/main.py` (WebSocket router + static file serving)
- `backend/app/ws_router.py`
- `frontend/src/App.tsx`
- `frontend/src/chart/ChartView.tsx`
- Build step to generate frontend/dist and include in backend deployment

Exit criteria:
- Live simulated candles render on chart.
- WebSocket reconnects successfully after disconnect.
- Protocol fields and schema docs are complete.

### Phase 1: ACP Price Agent Integration (No New UI Controls)
Goal: replace simulator-driven candles with ACP price-agent streaming and keep end-to-end data flow stable.

Deliverables:
- Backend subscribes to ACP test price agent at `http://localhost:8010` with fixed Phase 1 defaults: `SPY` + `1m`.
- Simulator is removed from live broadcast path.
- Frontend and backend stream handling migrate to ACP envelope semantics (`data`/`heartbeat`/`error`).
- Backend enforces ACP dedupe semantics for `ohlc` with key `(agent_id, id, rev)`.
- Basic upstream health visibility and reconnect behavior are added.
- ACP `/history` backfill stays out of scope for this phase.

Suggested implementation targets:
- `backend/app/ws_router.py` (ACP-driven producer + fanout)
- `backend/app/main.py` (startup/shutdown task wiring + health surface)
- `backend/app/acp_client.py` (new ACP WebSocket subscription client)
- `backend/tests/` (dedupe and ACP message-handling tests)

Exit criteria:
- With ACP test agent running on `localhost:8010`, chart receives continuous candle updates.
- Duplicate ACP `ohlc` revisions do not produce duplicate rendered updates.
- Backend tolerates temporary upstream disconnect and reconnects without restart.
- No new frontend subscription UI is required in this phase.

### Phase 1.5: MVP User Config Persistence (SQLite)
Goal: persist user setup so product state survives restart.

Deliverables:
- SQLite-backed user config store on backend.
- User-config REST endpoints (served by backend).
- Browser-side state hydration + save workflows.
- JSON import/export and backup flow via REST API.

Suggested implementation targets:
- `backend/app/storage/user_config.db`
- `backend/app/storage/config_models.py`
- `backend/app/routes/user_config.py` (REST endpoints)
- `frontend/src/settings/useUserConfig.ts` (browser client hook)

Exit criteria:
- Restarting app preserves subscriptions, indicators, layout, preferences.
- Concurrent edits are safely handled by versioned writes.
- Export/import round-trip works.

### Phase 2: Developer Reliability
Goal: make failures visible and testable before real provider integration.

Deliverables:
- Structured logs, metrics, tracing context.
- Schema validation at ingress.
- Chaos toggles (drop/reorder/duplicate) on simulated feed.
- CI smoke tests and persistence tests.

Suggested implementation targets:
- `backend/app/observability.py`
- `backend/tests/test_ws_reconnect.py`
- `backend/tests/test_user_config_persistence.py`
- `.github/workflows/ci.yml`

Exit criteria:
- Chaos scenarios validate dedupe/replay behavior.
- CI passes lint/typecheck/tests consistently.

### Phase 3: Real Price Agent Integration + Historical Backfill
Goal: integrate one real external price-data ACP agent (from another repository) for both live streaming and historical chart backfill, while preserving Odin’s internal contracts.

Deliverables:
- Use external real-data ACP agent via WebSocket for live `ohlc` updates.
- Keep ACP-compliant envelope handling (`data`/`heartbeat`/`error`) and dedupe behavior.
- Add backend REST history proxy endpoint that calls agent `/history`.
- Add frontend history load + live handoff (stitch history and stream without visible jump).
- Validate chart parity against at least one known live market platform for selected symbols/intervals.

Suggested implementation targets:
- `backend/app/acp_client.py`
- `backend/app/ws_router.py`
- `backend/app/main.py`
- `backend/app/routes/history.py`
- `frontend/src/history/fetchHistory.ts`
- `frontend/src/chart/ChartView.tsx`
- `frontend/src/stream/useEventStream.ts`

Exit criteria:
- With external agent running, Odin chart renders continuous real OHLC updates.
- History backfill loads from agent REST API and hands off cleanly to live stream.
- Duplicate OHLC revisions do not produce duplicate rendered bars.
- Candle values/time alignment are spot-checked against a live platform and are within accepted tolerance.

### Phase 4: Post-Integration Review + Next Planning Gate
Goal: finish validation, capture gaps, and collaboratively define the next roadmap before adding new scope.

Deliverables:
- End-to-end quality review of live + history behavior (functional and UX).
- List of known defects, protocol constraints, and technical debt discovered in Phase 3.
- Prioritized proposal for next phases based on measured needs.

Exit criteria:
- Team review completed with agreed findings and priorities.
- Updated follow-on phase plan approved.

## 8) Quality Gates and Verification
- Per phase:
  - Build and run with `docker compose up --build`.
  - Validate WebSocket connect/reconnect behavior.
  - Inject duplicate ACP OHLC revisions for `(agent_id, id, rev)` and verify no duplicate render.
- CI minimum:
  - Lint
  - Typecheck
  - Unit tests
  - Stream smoke test
- MVP acceptance criteria:
  - Chart latency target is defined and measured.
  - Reconnect recovery is validated.
  - Overlay alignment tolerance is validated under simulated disorder.

## 9) Risks and Mitigations
- Risk: ambiguous event semantics early.
  - Mitigation: protocol docs + ADR sign-off before coding phase work.
- Risk: premature complexity in storage/infra.
  - Mitigation: SQLite for config only in MVP; defer market durability.
- Risk: provider lock-in or schema drift.
  - Mitigation: strict adapter interface and canonical internal event schema.

## 10) Immediate Next Actions
1. Approve this specification.
2. Complete Phase 3 implementation (external live agent + history backfill).
3. Execute parity checks against live market platform for target symbols/timeframes.
4. Run Phase 4 review and finalize next-phase roadmap collaboratively.
