# Agent Chart Protocol (ACP)

- Spec ID: `ACP-0.3.0`
- Status: Draft (Authoritative in this repository)
- Last updated: 2026-03-08

## 1) Purpose

ACP defines a uniform contract for chart data agents used by Odin.

ACP standardizes:
- Session-scoped canonical candle distribution
- Historical backfill for price data (HTTP REST)
- Real-time streaming (WebSocket)
- Bidirectional backend/agent communication
- Error handling
- Heartbeats and liveness semantics
- Record-level dedup and replay behavior

This repository is the source of truth for ACP behavior and schema.

## 2) Scope and Non-Goals

### In Scope
- Single-symbol chart sessions
- One subscription maps to one logical indicator configuration
- Session-scoped sequence tracking and replay
- At-least-once delivery
- JSON payloads
- Base-URL agent discovery via metadata

### Non-Goals (for `ACP-0.3.0`)
- Multi-symbol subscriptions
- Authentication/authorization (local-only deployment)
- Binary serialization (e.g., Protobuf/MessagePack)
- Trading decision policy (execution gating is out of protocol scope)

## 3) System Roles

### Backend (Router / Session Manager)
- Owns canonical candle data per session
- Calls price agent REST `/history` for backfill
- Opens and manages WebSocket subscriptions to agents
- Pushes canonical candles to indicator agents
- Merges indicator outputs/events and forwards to UI
- Applies deduplication, gap handling, and replay

### Price Agent
- Ingests upstream broker/exchange data
- Exposes REST history endpoint
- Streams live OHLC updates over WebSocket
- Supports mutable candle revisions via `rev`

### Indicator Agent
- Exposes a discoverable indicator catalog at root metadata
- Receives canonical candles from backend (`history_push`, `tick_update`, `candle_closed`, `candle_correction`)
- Computes derived records and emits typed outputs
- Can host many logical indicators from one base URL

### Event Agent
- Produces event markers independent of candle push workflow

### UI
- Consumes merged backend stream
- Owns visual style configuration (color, shape, line style, panel placement)

## 4) Transport Model

### 4.1 Mandatory Interfaces by Agent Type
All agents MUST provide:
1. REST `/metadata`
2. WebSocket `/ws/live`

Only `agent_type=price` MUST provide:
3. REST `/history`

`agent_type=indicator|event` MAY provide `/history`; backend MUST treat it as optional capability.

Live data MUST NOT be polled over REST.

### 4.2 Connection Direction
- Backend connects to agents.
- Backend interrogates `/metadata` before subscribing.
- Backend requests history from price agents via REST.
- Backend and agents exchange live/session messages over WebSocket.

### 4.3 Discovery Model
- User input is a base agent URL (e.g. `http://localhost:8020`).
- Backend calls `GET {base_url}/metadata`.
- For indicator agents, `/metadata` MUST include an `indicators[]` catalog describing selectable indicators and supported parameters.
- Indicator selection occurs in subscription/config payload (`indicator_id`, params), not path routing.

## 5) Data Schemas

Supported output record schemas:
- `ohlc`
- `line`
- `event`
- `band`
- `histogram`
- `forecast`

All records MUST include:
- `id` (string; stable for dedup)
- `ts` (UTC ISO-8601 timestamp)

See the `schemas/` directory for normative JSON Schemas.

## 6) Supported Intervals

For `ACP-0.3.0`, valid interval values are:
- `1m` (1 minute)
- `2m` (2 minutes)
- `3m` (3 minutes)
- `4m` (4 minutes)
- `5m` (5 minutes)
- `10m` (10 minutes)
- `15m` (15 minutes)
- `20m` (20 minutes)
- `30m` (30 minutes)
- `1h` (1 hour)
- `2h` (2 hours)
- `4h` (4 hours)
- `8h` (8 hours)
- `12h` (12 hours)
- `1d` (1 day)
- `2d` (2 days)
- `1w` (1 week)
- `1M` (1 month)

`interval` in all ACP requests and messages MUST use one of these values.

## 7) Session Model

A `session_id` identifies one frontend chart window and its isolated backend state.

### 7.1 Session Rules
- `session_id` MUST be present on all WebSocket protocol messages.
- Backend sequence (`seq`) is monotonic per session and assigned by backend for backend-originated session messages.
- Sessions are isolated; replay, buffering, and correction fan-out are scoped per session.

## 8) WebSocket Messages

### 8.1 Backend -> Agent
- `subscribe`
- `unsubscribe`
- `reconfigure`
- `history_push`
- `tick_update`
- `candle_closed`
- `candle_correction`
- `resync_response`

### 8.2 Agent -> Backend
- `data`
- `heartbeat`
- `error`
- `history_response`
- `overlay_update`
- `overlay_marker`
- `resync_request`

## 9) Historical Backfill (REST)

Required only for `agent_type=price`:

`GET /history?symbol={symbol}&from={iso8601}&to={iso8601}&interval={interval}`

### 9.1 History Response Rules
- Response is a latest-snapshot view: one candle per timestamp (`id`) at its current best-known values.
- Records MUST be ordered ascending by `ts`.
- `from` is inclusive, `to` is exclusive.
- `interval` MUST be canonical.
- OHLC records MUST include `bar_state` and `rev`.
- History candles MAY be mutable over time; later REST reads may return higher `rev` or different values for the same `id`.

### 9.2 Mutable History Behavior
If backend detects that a REST history poll yields a higher `rev` (or changed values for same `id`), backend MUST:
1. Upsert canonical candle state for that `id`
2. Emit `candle_correction` to active indicator subscriptions in affected sessions
3. Preserve monotonic revision ordering for that candle

## 10) OHLC Lifecycle and Revision Semantics

`ohlc` records use mutable upsert semantics keyed by stable `id`.

### 10.1 Bar States
- `partial`: intrabar live update (forming candle)
- `provisional_close`: first closed value from stream close boundary
- `session_reconciled`: backend reconciliation stage after REST confirmation/polling (value may or may not change)
- `final`: terminal value (backend policy determines finalization timing)

### 10.2 State and Revision Rules
- `rev` MUST be monotonic per candle `id`.
- Higher `rev` for same `id` MUST be treated as upsert.
- Transition to `session_reconciled` MAY occur even when OHLCV is unchanged from `provisional_close`.
- `final` is terminal and MUST NOT be mutated afterward.

## 11) Delivery Semantics and Dedup

ACP uses at-least-once delivery.

Deduplication keys:
- Non-`ohlc` schemas: `(agent_id, id)`
- `ohlc`: `(agent_id, id, rev)`

Backend behavior:
- Duplicate non-`ohlc` key => ignore duplicate record
- Duplicate `ohlc` key `(agent_id, id, rev)` => ignore duplicate record
- Higher `rev` for same `(agent_id, id)` => upsert record

## 12) Sequence Tracking and Replay

### 12.1 Gap Detection
Agents receiving backend-sequenced session messages MUST track `last_seq_received` per subscription.

If a message arrives with `seq != last_seq_received + 1`, agent SHOULD send `resync_request` immediately.

### 12.2 Replay
Backend SHOULD maintain a rolling replay buffer per session.

On `resync_request`, backend SHOULD send `resync_response` with replay messages after `last_seq_received`.

If replay window is unavailable, backend SHOULD reset agent state with a new `history_push`.

## 13) Liveness and Health

Heartbeat defines stream liveness only.

Recommendations:
- Agent sends heartbeat every 5-15s per active subscription.
- Backend marks subscription stale if no heartbeat for 3x heartbeat interval.

## 14) Agent Metadata and Configuration (REST)

Required endpoint:

`GET /metadata`

Required fields include:
- `spec_version`
- `agent_id`
- `agent_name`
- `agent_version`
- `description`
- `agent_type` (`price` | `indicator` | `event`)
- `config_schema`
- `outputs`

Required for `agent_type=indicator`:
- `indicators[]`

Optional:
- `data_dependency` (`ohlc` | `event` | `none`)

### 14.1 Typed Outputs
`outputs[]` defines emitted data streams with stable `output_id` and schema typing.

Each output descriptor MUST include:
- `output_id`
- `schema`
- `label`
- `is_primary`

Indicator agents MAY emit multiple outputs from a single subscription (e.g., MACD line, signal line, crossover events).

### 14.2 Indicator Catalog
`indicators[]` entries define selectable indicators exposed by a single agent base URL.

Each entry MUST include:
- `indicator_id`
- `name`
- `description`
- `params_schema`
- `outputs`

## 15) Error Codes

Structured error codes for `ACP-0.3.0`:
- `INVALID_REQUEST`
- `INVALID_SYMBOL`
- `INVALID_INTERVAL`
- `INVALID_PARAMS`
- `UNSUPPORTED_OPERATION`
- `SUBSCRIPTION_NOT_FOUND`
- `SESSION_NOT_FOUND`
- `AGENT_OVERLOADED`
- `BACKFILL_TIMEOUT`
- `RESYNC_FAILED`
- `INTERNAL_ERROR`
