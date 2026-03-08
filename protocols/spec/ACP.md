# Agent Chart Protocol (ACP)

- Spec ID: `ACP-0.2.0`
- Status: Draft (Authoritative in this repository)
- Last updated: 2026-03-07

## 1) Purpose

ACP defines a uniform contract for chart data agents used by Odin.

ACP standardizes:
- Session-scoped canonical candle distribution
- Historical backfill (HTTP REST)
- Real-time streaming (WebSocket)
- Bidirectional backend/agent communication
- Error handling
- Heartbeats and liveness semantics
- Record-level dedup and replay behavior

This repository is the source of truth for ACP behavior and schema.

## 2) Scope and Non-Goals

### In Scope
- Single-symbol chart sessions
- One subscription maps to one agent + one session + one symbol + one interval + parameter set
- Session-scoped sequence tracking and replay
- At-least-once delivery
- JSON payloads

### Non-Goals (for `ACP-0.2.0`)
- Multi-symbol subscriptions
- Agent discovery/registry protocol
- Authentication/authorization (local-only deployment)
- Binary serialization (e.g., Protobuf/MessagePack)
- Trading decision policy (execution gating is out of protocol scope)

## 3) System Roles

### Backend (Router / Session Manager)
- Owns canonical candle data per session
- Calls price agent REST `/history` for backfill
- Opens and manages WebSocket subscriptions to agents
- Pushes canonical candles to overlay agents
- Merges overlays/events and forwards to UI
- Applies deduplication, gap handling, and replay

### Price Agent
- Ingests upstream broker/exchange data
- Exposes REST history endpoint
- Streams live OHLC updates over WebSocket
- Supports mutable candle revisions via `rev`

### Overlay Agent
- Receives canonical candles from backend (`history_push`, `tick_update`, `candle_closed`, `candle_correction`)
- Computes derived records and emits overlay updates
- Requests replay on sequence gaps via `resync_request`

### Event Agent
- Produces event markers independent of candle push workflow

### UI
- Consumes merged backend stream
- Renders price first, overlays asynchronously

## 4) Transport Model

### 4.1 Mandatory Interfaces
Each ACP agent MUST provide:
1. REST API for agent metadata (`/metadata`)
2. REST API for historical data (`/history`)
3. WebSocket API for live/protocol traffic

Live data MUST NOT be polled over REST.

### 4.2 Connection Direction
- Backend connects to agents.
- Backend interrogates `/metadata` before subscribing.
- Backend requests history from price agents via REST.
- Backend and agents exchange live/session messages over WebSocket.

No agent discovery protocol is defined in `ACP-0.2.0`.

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

## 6) Canonical Intervals

For `ACP-0.2.0`, valid interval values are:
- `1m`
- `5m`
- `15m`
- `30m`
- `1h`
- `4h`
- `1d`

`interval` in all ACP requests and messages MUST use one of these values.

## 7) Session Model

A `session_id` identifies one frontend chart window and its isolated backend state.

A session is uniquely defined by:
- `session_id`
- `symbol`
- `interval`
- active subscriptions

### 7.1 Session Rules
- `session_id` MUST be present on all WebSocket protocol messages in ACP-0.2.0.
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

Required endpoint:

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
2. Emit `candle_correction` to active overlay subscriptions in affected sessions
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
- `agent_type` (`price` | `overlay` | `event`)
- `config_schema`
- `output_schema`
- `overlay`

Optional:
- `data_dependency` (`ohlc` | `event` | `none`)

### 14.1 Multi-Output Agents
`output_schema` describes the agent's primary output record structure.

Agents MAY emit multiple distinct records or markers per session (e.g., a moving average pair indicator may emit two `line` records with different `id` values plus `event` crossover markers).

Backend MUST support 1-to-N output multiplicity within a single subscription using record-level `id` for deduplication and tracking.

Backend MUST:
1. Call `/metadata` before subscription
2. Enforce `spec_version` compatibility
3. Route behavior by `agent_type`

## 15) Error Codes

Structured error codes for `ACP-0.2.0`:
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
