# Export 2Y 1m CSV Plan

## Goal

Provide a one-button frontend flow that exports a single CSV for a user-selected date range (for example `2024-01-01` to present) containing:

- canonical candles (`row_type=candle`)
- overlay/signal records (`row_type=overlay`)

without requiring manual timeframe switching in the UI.

## Architecture (MVP)

- Backend runs asynchronous export jobs.
- Frontend starts job, polls status, and downloads CSV when complete.
- Export worker processes history in 6-month windows.
- For each window:
	- fetch price history from primary price agent `/history`
	- push window candles to active indicator agents via `history_push`
	- wait for settle using hybrid rule (min delay + stability polling)
- Worker writes one combined CSV file and marks job complete.

## Decisions Locked

- Output: single CSV file
- Run mode: async job + status polling + download URL
- Settle strategy: hybrid min delay + polling, hard timeout => fail job
- Session scope: current active backend session context

## Implemented in this pass

### Backend

- Added CSV export request model and defaults in `backend/app/main.py`.
- Added in-memory export job registry and background task runner in `backend/app/main.py`.
- Added helper logic in `backend/app/main.py` for:
	- date parsing (`YYYY-MM-DD` and ISO datetime)
	- 6-month window splitting
	- settle polling
	- CSV serialization
- Added endpoints:
	- `POST /api/sessions/{session_id}/exports/csv`
	- `GET /api/sessions/{session_id}/exports/csv/{job_id}`
	- `GET /api/sessions/{session_id}/exports/csv/{job_id}/download`
- Added non-OHLC accessor for export serialization in `backend/app/agent_data_store.py`:
	- `get_non_ohlc_records()`

### Frontend

- Added export API client methods in `frontend/src/workspace/workspaceApi.ts`:
	- `createCsvExportJob`
	- `getCsvExportJobStatus`
	- `getCsvExportDownloadUrl`
- Added topbar `Export CSV` button and export modal in `frontend/src/App.tsx`.
- Added date-range inputs, submit action, status polling, error state, and auto-download on completion in `frontend/src/App.tsx`.
- Bumped title string per project rule to `ODIN Market Workspace v1.30` in `frontend/src/App.tsx`.

### Contract docs

- Updated `docs/ui-backend-sync-contract.md` with **CSV Export Rules (v1)** indicating export jobs are out-of-band and do not modify revision sync semantics.

## CSV Schema (single file, bridge format)

Backtest bridge shape is **one row per candle timestamp**.

Core columns:

- `timestamp` (first column, candle key)
- `open`, `high`, `low`, `close`, `volume`
- `bar_state`, `symbol`, `interval`

Indicator columns are flattened dynamically using stable names:

- `agent_<agent_label>_<schema>.value`
- `agent_<agent_label>_<schema>.upper`
- `agent_<agent_label>_<schema>.lower`
- `agent_<agent_label>_<schema>.center`
- `agent_<agent_label>_<schema>.meta.<metadata_key>`

When an indicator has non-default output IDs, the output is included for uniqueness:

- `agent_<agent_label>_<output_id>_<schema>.<field>`

This format is intended for direct handoff to downstream backtest engines.

## Remaining follow-up (next agent)

1. Add cancellation endpoint (`POST .../{job_id}/cancel`) and task cancellation handling.
2. Add export file retention policy (TTL cleanup of old CSV files/jobs).
3. Add server-side guardrails for very large exports (max range, max rows, optional gzip).
4. Add backend tests for:
	 - job lifecycle transitions
	 - settle timeout failure
	 - deterministic CSV output ordering
5. Add frontend UX polish:
	 - optional progress detail (`chunk_window`) display
	 - success toast with row counts

## Validation checklist

- Start export from UI with valid date range.
- Confirm status transitions: `queued -> running -> completed`.
- Confirm failed settle transitions to `failed` with useful error.
- Download CSV and verify both candle and overlay rows are present.
- Verify no sync-domain regressions while polling export status.