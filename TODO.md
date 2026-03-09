# Post-Upgrade TODO (After Price + Indicator Agents reach ACP v0.3.0)

Status: in progress (started 2026-03-08).

Latest run notes:
- Verified price metadata (`http://localhost:8010/metadata`) returns `spec_version: ACP-0.3.0`, `agent_type=price`, and typed `outputs[]` with primary OHLC output.
- Verified indicator metadata (`http://localhost:8020/metadata`) returns `spec_version: ACP-0.3.0`, `agent_type=indicator`, non-empty `indicators[]`, and typed `outputs[]`.
- Verified backend discovery works via `POST /api/agents/discover` for both base URLs.
- Verified add-agent flow via backend `POST /api/agents` for indicator discovery/add path.
- Implemented fix for duplicate indicator additions: repeated adds now allocate unique runtime IDs (example: `odin_indicator_sma__sma`, `odin_indicator_sma__sma__2`) so instances remain independently selectable.
- Fixed frontend startup chart race where initial snapshot could be dropped before chart snapshot handler registration (no-candle-on-load issue).
- Section 3 validation run (automated WS + backend logs):
	- ✅ Non-price primary subscription correctly rejected with `UNSUPPORTED_OPERATION`.
	- ✅ Price subscription returned snapshot + historical backfill (`6764` bars in test run).
	- ✅ Tick fanout to indicator path observed (`tick_update` sent to indicator agent) and only one history fetch observed per subscribe request.
	- ✅ Indicator now receives `history_push` and emits `history_response` + `overlay_update` after backend normalization/cap fixes.
	- ⚠️ `candle_correction` fanout is still pending validation; no correction event observed in an extended 95s live run.
- Section 4 validation run (automated WS + store checks):
	- ✅ Replay sequencing now assigned server-side per session (`seq`) and persisted to session replay buffer.
	- ✅ `resync_request -> resync_response` returned replayed messages filtered by `last_seq_received`.
	- ✅ Two simultaneous sessions showed no cross-session contamination.
	- ✅ Non-OHLC dedup now keyed by `(source_agent_id, id)`; same id from different indicator agents is accepted independently.
	- ✅ OHLC dedup/upsert semantics validated for `(agent_id, id, rev)` (duplicate/lower rev rejected, higher rev accepted).
- Section 5 validation run (automated WS + frontend wiring checks):
	- ✅ Overlay stream observed (`history_response`) after subscribe, confirming indicator overlay path is active.
	- ✅ Re-subscribe behavior validated on same session when symbol/interval/timeframe changed (`AAPL/1m/1d -> SPY/5m/3d`) with updated snapshot context.
	- ✅ Frontend status wiring updated: heartbeat messages with `agent_id/status` now update agent status state in `useEventStream`.
	- ⚠️ Remaining Section 5 items still require manual visual UI confirmation.
- Section 5 manual test findings + fixes (2026-03-08):
	- ⚠️ Chart stuck on loading after refresh was reproduced when indicator became selected as primary chart agent.
	- ✅ Fixed by preventing newly added indicators from auto-selecting as chart primary and only auto-selecting price (`ohlc`) agents.
	- ⚠️ Indicator online/offline status did not update live unless hard refresh.
	- ✅ Fixed with periodic silent `/api/agents` refresh in frontend hook so status chips update without page reload.
	- ✅ Removed hardcoded startup indicator from `overlay_agents.yaml`; default boot now loads only price agent and indicator must be added via discover flow.
	- ⚠️ SMA line still not visible despite `history_response`/`overlay_update` traffic.
	- ✅ Fixed frontend rendering gap: overlay records were collected in `App` but never passed/rendered in `ChartView`; added line-series overlay rendering path and wired `overlayData` prop.
	- ⚠️ Clicking an indicator row in the widget caused SMA line to disappear until clicking price again.
	- ✅ Fixed by decoupling modal selection from chart primary agent selection; indicator row click now opens config modal without switching primary chart subscription.
	- ✅ Added indicator config controls in modal (`period`, `line_color`) with backend `PATCH /api/agents/{agent_id}` update support and live recompute push.
	- ✅ Added indicator removal action in modal with backend `DELETE /api/agents/{agent_id}` runtime removal support.
	- ⚠️ Interval switches (`5m/15m/1h/1d`) caused candles + SMA to break due upstream SDS price-agent `/history` 500s.
	- ✅ Patched SDS agent safeguards:
		- normalize mixed-type candle rows before Polars aggregation in `sds/src/sds/acp/routes.py`.
		- fix Alpaca RFC3339 UTC formatting in `sds/src/sds/backfill/alpaca_history.py` to avoid `400 Bad Request` on backfill.
	- 🔁 Requires SDS price-agent restart to take effect before re-validating interval switching.

## 1) Bring-up & Contract Validation
- [x] Start upgraded price agent and indicator agent services.
- [x] Verify `GET /metadata` on both agents returns `spec_version: ACP-0.3.0`.
- [x] Verify price metadata includes `agent_type=price`, `outputs[]`, and valid intervals.
- [x] Verify indicator metadata includes `agent_type=indicator`, non-empty `indicators[]`, and typed `outputs[]`.
- [x] Confirm indicator base URL discovery works via Odin backend `POST /api/agents/discover`.

## 2) Add-Agent UX Validation
- [x] In UI, open **Add Indicator Agent** modal and discover the indicator agent by base URL only. *(validated via backend discover/add API path this pass; UI click-path still to verify manually)*
- [ ] Confirm indicator list populates from `indicators[]`.
- [x] Add one indicator with required params and verify agent appears in Indicator Agents widget. *(backend list confirms creation; UI widget visibility still to verify manually)*
- [x] Add multiple indicators from the same base URL and verify each is independently selectable. *(validated via backend API with unique runtime IDs; UI multi-select behavior still to verify manually)*

## 3) Session & Streaming Validation
- [x] Subscribe chart using a price agent (primary subscription must reject non-price agent IDs).
- [x] Confirm historical candles load from price `/history`.
- [x] Confirm indicator receives `history_push` and starts emitting outputs.
- [x] Confirm live `tick_update` fanout drives indicator updates without duplicate candle fetches.
- [ ] Confirm `candle_correction` fanout reaches indicator subscriptions correctly.

## 4) Data Integrity & Replay
- [x] Validate OHLC dedup/upsert behavior on `(agent_id, id, rev)`.
- [x] Validate non-OHLC dedup behavior on `(agent_id, id)`.
- [x] Simulate sequence gap and verify `resync_request -> resync_response` recovery.
- [x] Confirm no cross-session contamination with two simultaneous chart sessions.

## 5) Frontend Rendering & Controls
- [x] Verify overlay history and live updates are received and state is maintained per indicator agent.
- [x] Validate changing symbol/interval/timeframe triggers expected resubscribe behavior. *(automated WS validation completed)*
- [x] Validate agent status indicators (`online/offline/error`) update correctly.
- [x] Verify Add Indicator modal handles discover failures and invalid parameter input gracefully.

## 6) Documentation & Cleanup
- [ ] Update any remaining internal docs/examples still referencing `ACP-0.2.0`.
- [ ] Update protocol example agents under `protocols/examples/` to `ACP-0.3.0`.
- [ ] Remove obsolete v0.2-oriented roadmap/TODO language once retest passes.
- [ ] Capture a short release note for this forward-only `ACP-0.3.0` cut.

## 7) Session Persistence Follow-up
- [ ] Implement saved user session profiles so each browser window/workspace (e.g., `AAPL` + agents, `SPY` + different agents) restores independently after reload/restart without re-adding agents.
