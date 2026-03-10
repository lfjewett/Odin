# Agent Chart Protocol (ACP)

- Spec ID: `ACP-0.4.1`
- Status: Draft (Authoritative in this repository)
- Last updated: 2026-03-10

## Changes from ACP-0.4.0 to ACP-0.4.1

**ACP-0.4.1 is a backward-compatible patch release.**

- Adds optional `metadata` object support across record schemas
- Adds `area` schema for shaded regions between `upper` and `lower`
- Adds optional area render hints via `metadata.render`:
  - `primary_color`
  - `secondary_color` (can be empty)
  - `opacity`
  - `transparency`
  - `gradient` options

`band` remains the preferred schema for multi-line envelope indicators (e.g., Bollinger Bands) without required shading semantics.

## Breaking Changes from ACP-0.3.0

**ACP-0.4.0 introduces mandatory chunking to support large-scale historical data (3+ years of 1-minute candles).**

- Chunking is now **MANDATORY** for all `history_push` and `history_response` messages
- Agents **MUST** declare transport limits in `/metadata`
- Backend **MUST** re-bootstrap subscriptions after WebSocket reconnect
- New error code: `PAYLOAD_TOO_LARGE` for protocol violations
- Performance requirements: agents must handle 1.5M+ records within 60 seconds

See `/protocols/docs/migrate-3-to-4.md` for migration guidance.

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

### Non-Goals (for `ACP-0.4.0`)
- Multi-symbol subscriptions
- Authentication/authorization (local-only deployment)
- Binary serialization (e.g., Protobuf/MessagePack)
- Trading decision policy (execution gating is out of protocol scope)
- Cross-agent data sharing or federation

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
- `area`
- `histogram`
- `forecast`

All records MUST include:
- `id` (string; stable for dedup)
- `ts` (UTC ISO-8601 timestamp)

Optional on all record schemas (`ACP-0.4.1`):
- `metadata` (object) for agent-specific extension data that may be consumed by trading logic and need not be charted.

For `area` schema, `metadata.render` MAY include optional rendering hints:
- `primary_color`: color used when top is over bottom
- `secondary_color`: color used when inverted (can be blank)
- `opacity`: 0..1 opacity multiplier
- `transparency`: 0..100 transparency percentage
- `gradient`: optional gradient config (e.g., enable flag, direction, start/end colors)

Render hints are advisory; UI implementations MAY ignore unsupported options.

`band` is intended for envelope-style indicators (e.g., Bollinger Bands) where lines are charted and shading is optional/implementation-defined.

See the `schemas/` directory for normative JSON Schemas.

## 6) Supported Intervals

For `ACP-0.4.0`, valid interval values are:
- `1m` (1 minute)
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
- `history_push` (with mandatory chunking support)
- `tick_update`
- `candle_closed`
- `candle_correction`
- `resync_response`

### 8.2 Agent -> Backend
- `data`
- `heartbeat`
- `error`
- `history_response` (with mandatory chunking support)
- `overlay_update`
- `overlay_marker`
- `resync_request`

### 8.3 Chunking Protocol (Mandatory in ACP-0.4.0)

All `history_push` and `history_response` messages **MUST** support chunking when record count exceeds `max_records_per_chunk`.

#### Chunk Fields
Chunked messages MUST include:
- `chunk_index` (integer, 0-based): Position of this chunk in sequence
- `total_chunks` (integer): Total number of chunks in this transfer
- `is_final_chunk` (boolean): true if this is the last chunk

#### Chunking Rules
1. Sender MUST split payloads when record count > `max_records_per_chunk` declared in agent metadata
2. Chunks MUST be sent sequentially with monotonic `chunk_index` starting at 0
3. Receiver MUST accumulate chunks until `is_final_chunk=true` before processing
4. `chunk_index` MUST equal current position (e.g., chunk 0, then 1, then 2...)
5. If `chunk_index` is missing or out of sequence, receiver MUST send `INVALID_REQUEST` error
6. Timeout between chunks defaults to 30 seconds (configurable at implementation level)
7. For single-chunk transfers, fields are optional but recommended for consistency

#### Example Chunked History Push
```json
// Chunk 0 of 3
{
  "type": "history_push",
  "spec_version": "ACP-0.4.0",
  "session_id": "session-123",
  "subscription_id": "sub-456",
  "agent_id": "sma_agent",
  "symbol": "SPY",
  "interval": "1m",
  "candles": [ /* 5000 records */ ],
  "count": 5000,
  "chunk_index": 0,
  "total_chunks": 3,
  "is_final_chunk": false
}

// Chunk 1 of 3
{
  "type": "history_push",
  // ... same envelope fields ...
  "candles": [ /* next 5000 records */ ],
  "count": 5000,
  "chunk_index": 1,
  "total_chunks": 3,
  "is_final_chunk": false
}

// Chunk 2 of 3 (final)
{
  "type": "history_push",
  // ... same envelope fields ...
  "candles": [ /* remaining 2000 records */ ],
  "count": 2000,
  "chunk_index": 2,
  "total_chunks": 3,
  "is_final_chunk": true
}
```

After receiving `is_final_chunk=true`, agent processes accumulated 12,000 candles and computes indicator, then sends chunked `history_response` back to backend.

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
- `spec_version` (MUST be `"ACP-0.4.0"`)
- `agent_id`
- `agent_name`
- `agent_version`
- `description`
- `agent_type` (`price` | `indicator` | `event`)
- `config_schema`
- `outputs`
- `transport_limits` (NEW in ACP-0.4.0, see below)

### 14.1 Transport Limits (NEW in ACP-0.4.0)

```json
{
  "transport_limits": {
**Field Definitions:**
- `max_records_per_chunk` (integer, required): Maximum records per chunk. MUST be between 1000-10000. Recommended: 5000.
- `max_websocket_message_bytes` (integer, required): Max single WebSocket message size in bytes. MUST be >= 1MB (1048576). Recommended: 10MB (10485760).
- `chunk_timeout_seconds` (integer, optional): Seconds to wait between chunks before timeout. Default: 30.
Backend MUST respect agent's `max_records_per_chunk` when sending `history_push`. If backend cannot chunk, it MUST NOT subscribe to agents requiring chunking.

`outputs[]` defines emitted data streams with stable `output_id` and schema typing.

Each output descriptor MUST include:
- `output_id`
- `schema`
- `label`
- `is_primary`

Indicator agents MAY emit multiple outputs from a single subscription (e.g., MACD line, signal line, crossover events).

### 14.3 Indicator Catalog
`indicators[]` entries define selectable indicators exposed by a single agent base URL.

Each entry MUST include:
- `indicator_id`
- `name`
- `description`
- `params_schema`
- `outputs`

## 15) Reconnect and State Management

### 15.1 WebSocket Reconnect Behavior

When a WebSocket connection is closed (normally or abnormally), backend MUST:

1. **Clear agent-side state assumption**: Treat agent as having no memory of prior session state
2. **Re-bootstrap on reconnect**: For each active subscription, backend MUST send complete sequence:
   - `subscribe` message
   - Full chunked `history_push` (entire canonical candle set for that subscription)
   - Resume normal `tick_update` / `candle_closed` flow

### 15.2 Agent Reconnect Behavior

Agents MUST:
- Clear all session state on WebSocket disconnect
- Reject `tick_update` / `candle_closed` messages for subscriptions that haven't received `history_push` after reconnect
- Send `SUBSCRIPTION_NOT_FOUND` error if update arrives before history bootstrap

### 15.3 Rationale

This ensures:
- No stale state after connection interruption
- Deterministic recovery from transport failures (like WebSocket code 1009 message too large)
- Agents don't emit incorrect overlays from partial state

## 16) Performance and Scale Requirements

### 16.1 Design Target

ACP-0.4.0 implementations MUST support:
- **3 years of 1-minute candles**: ~1,577,000 records (3 years × 252 trading days × 6.5 hours × 60 minutes)
- **Backfill time budget**: Full 3-year load within 60 seconds end-to-end
- **Per-chunk latency**: <100ms processing time (upsert + compute)
- **Memory efficiency**: <100 bytes per candle in agent memory

### 16.2 Agent Performance Obligations

Indicator agents MUST:
- Process each `history_push` chunk within 100ms (upsert + any incremental computation)
- Use O(n) or O(n log n) algorithms for candle insertion and indicator calculation
- Yield control to async event loop during long operations (e.g., every 1000 records)
- Log diagnostic timing for chunks exceeding 100ms processing time

### 16.3 Backend Performance Obligations

Backend MUST:
- Send `history_push` chunks without artificial delays between chunks
- Pipeline chunk transmission (don't wait for agent processing confirmation between chunks)
- Accumulate `history_response` chunks in memory before forwarding to UI
- Support at least 10 concurrent agent subscriptions processing 3-year data simultaneously

### 16.4 WebSocket Transport Tuning

Implementations SHOULD:
- Configure WebSocket frame size limits to 16MB+
- Use message compression (permessage-deflate) when supported
- Set TCP socket buffer sizes appropriately for high throughput (e.g., SO_SNDBUF/SO_RCVBUF >= 256KB)

## 17) Error Codes

Structured error codes for `ACP-0.4.0`:
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
- `PAYLOAD_TOO_LARGE` (NEW in ACP-0.4.0): Sent when message exceeds declared `max_websocket_message_bytes` or violates chunking protocol
- `CHUNK_SEQUENCE_ERROR` (NEW in ACP-0.4.0): Sent when chunks arrive out of order or with gaps
