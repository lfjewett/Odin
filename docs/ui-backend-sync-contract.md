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
- VWAP display overrides (`vwap_line_color`, `vwap_upper_band_color`, `vwap_lower_band_color`, `vwap_line_style`, `vwap_upper_band_style`, `vwap_lower_band_style`) are UI-managed config fields and must persist in backend agent `config` round-trips while being excluded from indicator runtime subscribe params.
- `visible` remains a UI-managed config field and must persist in backend agent `config` round-trips while being excluded from indicator runtime subscribe params.
- `area_fill_mode` (`conditional|solid`), `area_fill_opacity` (`0..100`, default `50`), `area_conditional_up_color`, `area_conditional_down_color`, `area_use_source_style` (`boolean`), and `area_show_labels` (`boolean`) are UI-managed config fields for area overlays and must persist in backend agent `config` round-trips while being excluded from indicator runtime subscribe params.
- If schema matching is unavailable, frontend may fall back to rendering existing persisted config keys except `line_color`.
- Session variable discovery for area outputs must expose both `upper/lower` and any numeric `record.metadata` fields using stable DSL-safe canonical names. For duplicate label cases like `Gungnir:Gungnir`, canonical names collapse to `Gungnir:upper`, `Gungnir:lower`, and `Gungnir:dist`. Strategy evaluation must resolve metadata-backed variables from each record's `metadata` payload and keep legacy alias compatibility for previously saved rules that still reference `Gungnir:Gungnir:upper|lower` or `...:meta_<field>`.

## Backend-Authoritative Area Zone Rules (ACP-0.4.2)

- Support/resistance zone indicators must emit canonical `area` records from backend/agent on candle timestamps; UI must not synthesize or infer missing zone values.
- For multi-zone indicators, each zone must be partitioned with stable `output_id` values (for example `zone_1` ... `zone_8`) so backend storage, export columns, and DSL variable discovery remain deterministic.
- Numeric confidence intended for trade logic must be emitted in record metadata (recommended key: `metadata.confidence`) by backend/agent and consumed by backend trade evaluation/export; UI may only display it.
- Optional text labels (recommended key: `metadata.label`) and optional render hints (`metadata.render.*`) are display metadata and must not be required for backend trade correctness.
- Backend canonical storage must treat overlay updates as authoritative for matching candle/output identity; UI replay/state should follow backend snapshots/events rather than local heuristics.
- When `config.area_use_source_style === true`, frontend must prefer per-record `metadata.render` colors/opacities for area overlays; when false, frontend may apply UI-managed area fill overrides instead.
- When `config.area_show_labels !== false`, frontend may render `metadata.label` for active area zones; when false, label rendering must be suppressed without affecting backend-canonical records.

## Overlay Output Identity Rules (ACP-0.4.3)

- For `ACP-0.4.3`, all non-OHLC overlay records MUST include non-empty `output_id` on every emitted record (`history_response.overlays[]`, `overlay_update.record`, `overlay_marker.record`).
- For multi-output indicators, record `output_id` MUST map to stable metadata output descriptors and remain deterministic through the subscription lifecycle.
- Backend enforces this for incoming `ACP-0.4.3` payloads and returns protocol error `INVALID_MESSAGE` when `output_id` is missing/empty.

## Recovery Rules

- Session websocket `seq` values are session-global across replay-buffered live messages (`data`, `candle_correction`, `overlay_update`, `overlay_marker`, and replayed history chunks when present). Frontend gap detection must therefore advance sequence tracking on all sequenced session messages, not only OHLC candles.
- Subscription failures (`SUBSCRIPTION_NOT_FOUND`) should trigger backend recovery/forced resubscribe.
- `SUBSCRIPTION_NOT_FOUND` emitted during immediate post-subscribe `history_push` is treated as transient: backend auto-recovers (forced resubscribe + replayed history) and should not leave indicator status stuck in `error` when recovery succeeds.
- Invalid indicator params are sanitized/clamped before subscribe.
- Trade stale state without payload can trigger throttled frontend auto-heal reapply.
- Primary chart resubscribe now forces indicator-agent resubscribe (`force=True`) before `history_push` to avoid stale indicator-internal buffers across timeframe changes.
- Reconnect rebootstrap `history_push` is indicator-only; non-indicator agents (for example primary price agents) must never receive `history_push` during reconnect recovery.
- Backend subscribe handling is epoch-guarded per `session_id`; if a newer subscribe request starts (symbol/interval/timeframe switch), older in-flight subscribe flows must not emit late `snapshot`/`history_push` messages for stale intervals.
- On resubscribe for an existing `session_id`, backend rotates to a new `subscription_id` version (per agent/session) so agents treat source switches as fresh subscriptions without relying on timing-sensitive pre-unsubscribe ordering.
- Frontend overlay rendering is clipped to the current candle time window (derived from the active snapshot/canonical chart data) so out-of-window overlay history cannot persist in view after timeframe switches.
- **Sub-graph pane routing**: Overlay records are rendered in a separate sub-graph pane (below the main chart) when any of the following apply: (a) `schema === "histogram"`, (b) the first overlay record's `metadata.subgraph === true` (agent opt-in), or (c) the subscription's `config.force_subgraph === true` (UI-level user override). The UI override takes the highest priority and is toggled via the indicator's configure screen — both at creation time (`AddIndicatorAgentModal`) and on subsequent edits (`AgentConfigModal`). `force_subgraph` is a UI-managed config field (like `visible` and `line_color`) and must be excluded from agent runtime subscribe params. Agents that produce values on a non-price scale (e.g. ATR, RSI, MACD histograms) SHOULD set `"metadata": {"subgraph": true}` in every emitted `OverlayRecord` as a default, but users may override this at any time without modifying agent code. Pane index assignment is stable for the lifetime of the chart session and is reset on chart re-creation (symbol/interval/timeframe change).
- **Sub-graph pane user sizing**: Frontend initializes sub-graph pane stretch only when a pane is first created in the current chart session. Subsequent overlay refreshes (including periodic agent polling/reconciliation) must not re-apply pane stretch factors, so manual drag-resized pane heights remain intact.
- Frontend clears `tradeMarkers` and `tradePerformance` on subscribe-key changes (`session/symbol/interval/timeframe`) and marks trade domain syncing before recompute.
- Frontend auto-apply no longer trusts hydrated local trade cache as authoritative for a new subscribe key; it recomputes from backend once candles are loaded.
- Frontend chart marker rendering is clipped to the active candle time window so stale trade markers from prior ranges cannot render outside the current chart domain.

## Backend-Authoritative Viewport Paging Rules

- For long historical windows where full client-side rendering is not desirable (currently intended for 3M/6M 1m sessions), backend may retain the full subscribed timeframe while sending only a viewport slice to the frontend.
- `subscribe_request.viewport_days` is an optional UI hint indicating the desired rendered window size. Backend still ingests/stores the full `timeframe_days` history for the session and continues to drive indicators/trade evaluation from that full retained dataset.
- Initial `snapshot` may therefore contain only the latest viewport slice while also including full-range metadata: `total_bars`, `range_start_ts`, `range_end_ts`, `viewport_from_ts`, `viewport_to_ts`, `viewport_days`, `is_viewported`, `is_latest`, `follow_live`, and `slider_value`.
- Frontend viewport navigation uses backend-authoritative slices from `GET /api/sessions/{session_id}/viewport`; frontend must treat each returned slice as the full source of truth for currently rendered candles, overlays, and trade markers.
- For `1M`, `3M`, and `6M` timeframe selections on `1m` intervals, frontend week paging UI is shown next to the interval/timeframe control as `Week X of N` with left/right arrows.
- Week paging windows are backend viewport requests using fixed 7-day windows (ET day boundaries), clamped to the retained session range.
- The newest page (`Week 1`) is the most recent retained 7-day window (or partial week at range boundary).
- Left arrow loads older week pages (`Week 2`, `Week 3`, ...); right arrow moves toward newer pages, with fetches still performed through `GET /api/sessions/{session_id}/viewport`.
- Week-page fetches should bypass stale local viewport cache and force-refresh from backend so overlays/indicators are complete when revisiting pages.
- Frontend viewport slider scrubbing should be locally responsive (client-side draft thumb position) while viewport fetches are debounced/coalesced; intermediate scrub requests may skip adjacent-page prefetch to avoid request storms.
- In paged viewport mode, the scrubber controls only the currently loaded viewport's visible logical range (local chart pan). Crossing retained-range boundaries is explicit via page arrows / `Latest`, which are the controls that trigger backend viewport fetches.
- When the visible local chart range reaches the left or right bookend of the currently loaded slice and another slice is available, frontend may render a full-height viewport boundary cue (for example cyan shading). This cue is display-only and must not alter backend-canonical data state.
- Page-arrow navigation should anchor to the user's currently visible left/right boundary time so paging preserves seam context instead of jumping to unrelated positions.
- Frontend must merge repeated `history_response` batches progressively by overlay record identity instead of replacing previously received records for the same series. This preserves incremental indicator backfill behavior for the active viewport while later backend viewport refreshes remain authoritative.
- When backend returns a refresh for the same active viewport slice, frontend should avoid clearing series and preserve progressive backfill semantics. Frontend may short-circuit to full-series replacement only when an incoming batch clearly covers the currently rendered window for that series (`incoming.first_ts <= existing.first_ts` and `incoming.last_ts >= existing.last_ts`), since backend canonical storage is authoritative.
- When a session viewport is paged away from the latest range, backend suppresses forwarding of live candle and overlay updates that fall outside the active viewport. When the viewport returns to the latest range, live forwarding resumes (`follow_live=true`).
- Overlay history forwarded from backend to frontend must be sliced to the active viewport when viewport paging is enabled. Backend canonical overlay storage remains full-range and unchanged.
- UI navigation in viewport mode is split: scrubber pans within the current loaded slice, while arrows / `Latest` move between backend slices across the retained range.
- For long `1m` sessions (`1M`/`3M`/`6M`), frontend sends `subscribe_request.viewport_days=7` so backend keeps full retained data for indicator/trade computation while UI renders a one-week slice at a time.

## Indicator History Loading UX Rules

- On primary chart subscribe-key changes (`agent_id/symbol/interval/timeframe`), frontend starts a new indicator history loading cycle for configured indicator subscriptions (`agent_type === "indicator"`).
- Frontend progress denominator is the configured indicator count for that cycle; progress numerator increments on first `history_response` received per indicator agent id.
- Timeout window starts only after the matching chart `snapshot` is received for the active subscription.
- If all indicators respond before timeout, frontend dismisses the loading overlay immediately.
- If not all indicators respond within 30 seconds after snapshot, frontend dismisses the loading overlay, shows `Some agent data may be delayed.` for 5 seconds, and continues merging late indicator history silently.
- This progress UX is frontend-derived from existing websocket stream events and does not require a dedicated backend progress event type.

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
- Export workers fetch OHLC history in backend windows for transport safety, but indicator hydration for export correctness must run against the fully assembled canonical export history (single cumulative `history_push` per indicator) to preserve long-lookback continuity across window boundaries.
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
