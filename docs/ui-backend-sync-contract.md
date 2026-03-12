# UI/Backend Sync Contract

This document defines the runtime synchronization contract between backend state and frontend UI reconciliation.

## Purpose

Ensure UI state remains correct without manual refresh by using domain revisions, invalidation events, and authoritative sync snapshots.

## Domains

The backend tracks independent monotonic revisions for:

- `agent`
- `overlay`
- `trade`
- `workspace`

A higher revision always supersedes a lower revision for the same domain.

## Message Types

### 1) `state_event`

Backend -> Frontend notification that a domain changed.

Required fields:

- `type`: `"state_event"`
- `domain`: one of `agent | overlay | trade | workspace`
- `reason`: machine-readable reason string
- `revision`: revision for the changed domain after increment
- `server_revisions`: full revision map snapshot
- `server_ts`: server timestamp in milliseconds

Optional fields:

- `session_id`: when event is session-scoped
- `payload`: partial domain payload (may be omitted)

Semantics:

- Frontend marks domain stale when `server_revisions[domain]` exceeds local known revision.
- Frontend may reconcile immediately from payload when included.
- Frontend should request/await sync snapshot for authoritative merge.

### 2) `client_sync`

Frontend -> Backend request for authoritative state when stale domains are detected or on periodic reconciliation.

Required fields:

- `type`: `"client_sync"`
- `client_revisions`: client-known revision map

Optional fields:

- `session_id`: currently focused session

Semantics:

- Backend compares revisions and returns a `sync_snapshot`.

### 3) `sync_snapshot`

Backend -> Frontend authoritative state response to `client_sync`.

Required fields:

- `type`: `"sync_snapshot"`
- `server_revisions`: full server revision map
- `server_ts`: server timestamp in milliseconds

Optional domain payloads (included when needed):

- `agent_list`
- `overlay_sessions`
- `trade_sessions`
- `workspace`

Semantics:

- Frontend must treat this as source of truth.
- Frontend applies included payloads and advances local revisions to `server_revisions`.
- If a stale domain has no payload, frontend keeps stale/syncing handling and may trigger domain-specific recovery (e.g., trade strategy reapply for active session).

## Conflict Rules

1. Revisions are monotonic per domain and server-authoritative.
2. Payload data is accepted only when it matches/supersedes local domain revision.
3. UI must never clear stable domain data on invalidation without either:
   - a replacement payload, or
   - an active recovery path.

## Indicator Config Editing Rules

- Backend agent payloads must continue to include the current indicator `config` and the discovered `indicators` catalog with each indicator's `params_schema`.
- Backend agent payloads for indicator subscriptions must also include `selected_indicator_id` so frontend editing and backend resubscribe both target the same catalog entry.
- Frontend indicator edit forms must derive editable fields from the matched indicator `params_schema`, not from hardcoded parameter names.
- Frontend indicator edit forms must render enum-constrained params (`params_schema.<field>.enum`) as dropdown/select controls so users can discover valid values (for example ATR `mode`).
- Frontend indicator edit forms must always expose optional `aggregation_interval` in `params` for all indicators, even when omitted from `params_schema`.
- Frontend must validate `aggregation_interval` against the active subscription source `interval` before PATCH (`same unit`, `>= source`, integer multiple, canonical enum).
- Backend indicator subscribe/reconcile flows must send the selected `indicator_id` to remote agents; matching by URL or agent path is not sufficient.
- `line_color` remains a UI-managed config field and should be editable even when it is not declared in `params_schema`.
- `visible` remains a UI-managed config field and must persist in backend agent `config` round-trips while being excluded from indicator runtime subscribe params.
- `area_fill_mode` (`conditional|solid`), `area_fill_opacity` (`0..100`, default `50`), `area_conditional_up_color`, and `area_conditional_down_color` are UI-managed config fields for area overlays and must persist in backend agent `config` round-trips while being excluded from indicator runtime subscribe params.
- If schema matching is unavailable, frontend may fall back to rendering existing persisted config keys except `line_color`.
- Session variable discovery for area outputs must expose both `upper/lower` and any numeric `record.metadata` fields using stable DSL-safe canonical names. For duplicate label cases like `Gungnir:Gungnir`, canonical names collapse to `Gungnir:upper`, `Gungnir:lower`, and `Gungnir:dist`. Strategy evaluation must resolve metadata-backed variables from each record's `metadata` payload and keep legacy alias compatibility for previously saved rules that still reference `Gungnir:Gungnir:upper|lower` or `...:meta_<field>`.

## Backend-Authoritative Area Zone Rules (ACP-0.4.2)

- Support/resistance zone indicators must emit canonical `area` records from backend/agent on candle timestamps; UI must not synthesize or infer missing zone values.
- For multi-zone indicators, each zone must be partitioned with stable `output_id` values (for example `zone_1` ... `zone_8`) so backend storage, export columns, and DSL variable discovery remain deterministic.
- Numeric confidence intended for trade logic must be emitted in record metadata (recommended key: `metadata.confidence`) by backend/agent and consumed by backend trade evaluation/export; UI may only display it.
- Optional text labels (recommended key: `metadata.label`) and optional render hints (`metadata.render.*`) are display metadata and must not be required for backend trade correctness.
- Backend canonical storage must treat overlay updates as authoritative for matching candle/output identity; UI replay/state should follow backend snapshots/events rather than local heuristics.

## Recovery Rules

- Subscription failures (`SUBSCRIPTION_NOT_FOUND`) should trigger backend recovery/forced resubscribe.
- `SUBSCRIPTION_NOT_FOUND` emitted during immediate post-subscribe `history_push` is treated as transient: backend auto-recovers (forced resubscribe + replayed history) and should not leave indicator status stuck in `error` when recovery succeeds.
- Invalid indicator params are sanitized/clamped before subscribe.
- Trade stale state without payload can trigger throttled frontend auto-heal reapply.
- Primary chart resubscribe now forces indicator-agent resubscribe (`force=True`) before `history_push` to avoid stale indicator-internal buffers across timeframe changes.
- Backend subscribe handling is epoch-guarded per `session_id`; if a newer subscribe request starts (symbol/interval/timeframe switch), older in-flight subscribe flows must not emit late `snapshot`/`history_push` messages for stale intervals.
- On resubscribe for an existing `session_id`, backend rotates to a new `subscription_id` version (per agent/session) so agents treat source switches as fresh subscriptions without relying on timing-sensitive pre-unsubscribe ordering.
- Frontend overlay rendering is clipped to the current candle time window (derived from the active snapshot/canonical chart data) so out-of-window overlay history cannot persist in view after timeframe switches.
- **Sub-graph pane routing**: Overlay records are rendered in a separate sub-graph pane (below the main chart) when any of the following apply: (a) `schema === "histogram"`, (b) the first overlay record's `metadata.subgraph === true` (agent opt-in), or (c) the subscription's `config.force_subgraph === true` (UI-level user override). The UI override takes the highest priority and is toggled via the indicator's configure screen — both at creation time (`AddIndicatorAgentModal`) and on subsequent edits (`AgentConfigModal`). `force_subgraph` is a UI-managed config field (like `visible` and `line_color`) and must be excluded from agent runtime subscribe params. Agents that produce values on a non-price scale (e.g. ATR, RSI, MACD histograms) SHOULD set `"metadata": {"subgraph": true}` in every emitted `OverlayRecord` as a default, but users may override this at any time without modifying agent code. Pane index assignment is stable for the lifetime of the chart session and is reset on chart re-creation (symbol/interval/timeframe change).
- **Sub-graph pane user sizing**: Frontend initializes sub-graph pane stretch only when a pane is first created in the current chart session. Subsequent overlay refreshes (including periodic agent polling/reconciliation) must not re-apply pane stretch factors, so manual drag-resized pane heights remain intact.
- Frontend clears `tradeMarkers` and `tradePerformance` on subscribe-key changes (`session/symbol/interval/timeframe`) and marks trade domain syncing before recompute.
- Frontend auto-apply no longer trusts hydrated local trade cache as authoritative for a new subscribe key; it recomputes from backend once candles are loaded.
- Frontend chart marker rendering is clipped to the active candle time window so stale trade markers from prior ranges cannot render outside the current chart domain.

## Trade Strategy Persistence Rules

- Trade strategy save/load payloads are array-first and must round-trip `long_entry_rules`, `long_exit_rules`, `short_entry_rules`, and `short_exit_rules` without cross-field remapping.
- Legacy single-value columns (`long_entry_rule`, `short_entry_rule`) are compatibility fallbacks only and must map to the first value of their matching side/phase (`long_entry_rules[0]`, `short_entry_rules[0]`) — never to exit rules.
- Strategy list/get queries must include both legacy and array columns so UI reload always reflects the latest saved multi-rule strategy text.

## Research Expression Rules (v1)

- Research expression evaluation is request/response only via `POST /api/sessions/{session_id}/research/evaluate`.
- Frontend evaluation trigger is explicit user action (`Apply`) rather than background auto-run.
- Frontend preserves the in-progress research draft text and selected output schema between modal open/close cycles within the running app session.
- On successful `Apply` and on `Clear Overlay`, the modal closes immediately; `Clear Overlay` removes all research series.
- v1 research state is intentionally client-transient and does not participate in revision domains (`agent | overlay | trade | workspace`).
- Backend returns normalized overlay records (`line`, `histogram`, or `area`) from the current session candle+indicator context; frontend renders them through existing overlay chart pathways without websocket subscription state.
- `Apply` supports single-line and multi-line programs. Multi-line format accepts `line|area|histogram = <expression>` per line and optional directives like `@ COLOR.RED`, `@ COLOR.#22c55e`, `@ TREND`, and `@ SUBGRAPH.<id>` (alias `@ PANE.<id>`).
- Research overlays default to a shared subgraph group (`subgraph_group=1`), so multiple research lines render in the same pane unless a different subgraph group is specified.
- Frontend normalizes unary negative numeric literals (for example `-0.20`) to backend-compatible DSL arithmetic (`0 - 0.20`) before evaluation.
- Research helper token buttons (built-ins and color directives) perform client-only text insertion at the current cursor position in the editor and do not alter backend sync domains.
- Frontend must treat research series as ephemeral UI output (replace on new run, clear on explicit clear/reload) and must not persist them in workspace snapshots.

## CSV Export Rules (v1)

- CSV exports are asynchronous backend jobs initiated via REST and are out-of-band from websocket stream reconciliation.
- Export job lifecycle endpoints (`create/status/download`) must not mutate sync domain revisions (`agent | overlay | trade | workspace`) by themselves.
- Export workers may create temporary backend sessions for chunk processing, but those sessions are internal and must be cleaned up after job completion/failure.
- Frontend polling of export job status is independent from `client_sync` reconciliation and must not be used to infer domain freshness.
- Frontend should not rely solely on popup/new-tab behavior for downloads; it should trigger direct download and offer a visible retry action when a job is completed.

## Telemetry Rules (Phase 0)

- Runtime telemetry is exposed by backend at `GET /api/runtime/telemetry` for operational baselining (ingest counts/rates, active overlay records by session, trade recompute latency summaries).
- Telemetry endpoint reads are out-of-band observability operations and must not increment or reconcile sync domain revisions.

## Implementation References

Backend:

- `backend/app/main.py`
  - domain revisions and `emit_state_event`
  - `handle_client_sync_request` (`sync_snapshot`)
  - websocket route handling `client_sync`
- `backend/app/agent_connection.py`
  - forced resubscribe support (`subscribe(..., force=True)`)

Frontend:

- `frontend/src/stream/useEventStream.ts`
  - receives `state_event` and `sync_snapshot`
  - sends periodic `client_sync`
- `frontend/src/hooks/useSyncCoordinator.ts`
  - local revision/stale-domain tracking
- `frontend/src/App.tsx`
  - domain reconciliation and trade auto-heal behavior

## Change Policy

Any UI/backend change that affects state lifecycle, revisions, subscriptions, snapshots, or reconciliation logic must update this contract document in the same PR.
