# ACP Enhancement Specification for Odin Platform

**Target Audience**: Agentic AI maintaining the ACP (Agent Chart Protocol) specification  
**Purpose**: Extend ACP-0.1.0 to support bidirectional agent communication and session-based canonical data distribution  
**Proposed Version**: ACP-0.2.0

---

## Executive Summary

The current ACP-0.1.0 specification defines a protocol where **agents are data sources** that the backend subscribes to. This works well for price agents that ingest broker feeds and produce OHLC candles.

However, Odin's architecture requires a **bidirectional model** where:
1. The **backend owns canonical candle data** for each chart session
2. **Overlay agents are data consumers** that receive candles from the backend
3. **Overlay agents compute and return derived data** (indicators, patterns, sentiment) to the backend
4. Multiple chart windows (sessions) operate independently with isolated state
5. **Reliability primitives** ensure no data loss during network interruptions

This document specifies the extensions needed to achieve this architecture while maintaining backward compatibility with ACP-0.1.0 price agents.

---

## Current ACP-0.1.0 Limitations

### 1. Unidirectional Data Flow
- Agents produce data → Backend consumes data
- No mechanism for backend to push canonical data to agents
- Overlay agents can't receive the same candle stream that price agents produce

### 2. No Session Concept
- Protocol assumes single continuous subscription per agent
- No way to isolate multiple chart windows (sessions) with different symbols/intervals
- Can't guarantee all agents in a session see identical candle data

### 3. No Reliability Primitives
- Agents emit `seq` field but protocol doesn't define gap handling
- No resync mechanism when messages are lost
- No way to replay missed messages

### 4. No Candle Correction Support
- Brokers reconcile OHLC data post-close and at EOD
- No message type to notify agents when past candles are corrected
- Agents compute indicators on potentially stale data

### 5. Overlay Agent Pattern Not Defined
- Current schemas (line, event, band, histogram) describe data shape
- No protocol for how overlay agents receive candles and return computed results
- `HISTORY_RESPONSE` concept exists in brief but not in ACP spec

---

## Proposed Enhancements for ACP-0.2.0

### Enhancement 1: Bidirectional Message Flow

**Change**: Introduce new message types for backend-to-agent communication

#### New Backend → Agent Messages

##### HISTORY_PUSH
Backend pushes complete historical candle array to an overlay agent when a session starts.

```json
{
  "type": "history_push",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "symbol": "SPY",
  "interval": "1m",
  "candles": [
    {
      "id": "SPY:1m:1709827200",
      "seq": 28497000,
      "rev": 0,
      "bar_state": "final",
      "ts": "2024-03-07T14:00:00Z",
      "open": 512.45,
      "high": 512.67,
      "low": 512.40,
      "close": 512.55,
      "volume": 145234
    }
    // ... more candles
  ],
  "count": 2730
}
```

**Agent Behavior**:
- Load candles into memory
- Compute indicator values for entire history
- Respond with `HISTORY_RESPONSE` (see below)

---

##### TICK_UPDATE
Backend notifies overlay agents of a live quote update (candle is still forming, not yet closed).

```json
{
  "type": "tick_update",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "seq": 10042,
  "candle": {
    "id": "SPY:1m:1709827260",
    "seq": 28497001,
    "rev": 5,
    "bar_state": "partial",
    "ts": "2024-03-07T14:01:00Z",
    "open": 512.55,
    "high": 512.60,
    "low": 512.52,
    "close": 512.58,
    "volume": 8234
  }
}
```

**Agent Behavior**:
- Update running indicator state with new tick
- Optionally emit `OVERLAY_UPDATE` with new computed value
- Wait for `CANDLE_CLOSED` before finalizing

---

##### CANDLE_CLOSED
Backend notifies agents that a candle has officially closed (final OHLCV values are available).

```json
{
  "type": "candle_closed",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "seq": 10043,
  "candle": {
    "id": "SPY:1m:1709827260",
    "seq": 28497001,
    "rev": 8,
    "bar_state": "final",
    "ts": "2024-03-07T14:01:00Z",
    "open": 512.55,
    "high": 512.61,
    "low": 512.52,
    "close": 512.59,
    "volume": 12456
  }
}
```

**Agent Behavior**:
- Finalize indicator computation for this candle
- Emit `OVERLAY_UPDATE` with final value
- Store candle in history

---

##### CANDLE_CORRECTION
Backend notifies agents that a previously closed candle has been reconciled/corrected by the broker.

```json
{
  "type": "candle_correction",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "seq": 10044,
  "candle": {
    "id": "SPY:1m:1709827200",
    "seq": 28497000,
    "rev": 1,
    "bar_state": "reconciled",
    "ts": "2024-03-07T14:00:00Z",
    "open": 512.46,
    "high": 512.68,
    "low": 512.41,
    "close": 512.56,
    "volume": 145890
  },
  "reason": "broker_reconciliation"
}
```

**Agent Behavior**:
- Update stored candle with corrected values
- Recompute affected indicator values (if stateful)
- Optionally emit corrected `OVERLAY_UPDATE` messages

**New Bar States**:
- `partial`: Candle is forming (intrabar updates)
- `final`: Candle closed (initial close from streaming API)
- `reconciled`: Candle corrected by REST API reconciliation
- `eod_final`: End-of-day final value (T+1 settlement complete)

---

##### RESYNC_RESPONSE
Backend replays buffered messages to agent after gap detected.

```json
{
  "type": "resync_response",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "last_seq_received": 10040,
  "messages": [
    {
      "type": "tick_update",
      "seq": 10041,
      "candle": { /* ... */ }
    },
    {
      "type": "candle_closed",
      "seq": 10042,
      "candle": { /* ... */ }
    }
  ]
}
```

**Agent Behavior**:
- Process messages in order
- Update internal sequence tracking
- Resume normal operation

---

#### New Agent → Backend Messages

##### HISTORY_RESPONSE
Agent returns computed overlay values for the entire historical range after receiving `HISTORY_PUSH`.

```json
{
  "type": "history_response",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "schema": "line",
  "overlays": [
    {
      "id": "ema-28497000",
      "ts": "2024-03-07T14:00:00Z",
      "value": 511.23
    },
    {
      "id": "ema-28497001",
      "ts": "2024-03-07T14:01:00Z",
      "value": 511.45
    }
    // ... one per candle
  ],
  "metadata": {
    "period": 20,
    "seed_bars": 20,
    "computation_time_ms": 45
  }
}
```

**Backend Behavior**:
- Store overlay data keyed by `agent_id` and `session_id`
- Merge with candle timeline
- Forward to frontend clients in that session

---

##### OVERLAY_UPDATE
Agent returns a single new computed value after processing a tick or closed candle.

```json
{
  "type": "overlay_update",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "schema": "line",
  "record": {
    "id": "ema-28497002",
    "ts": "2024-03-07T14:02:00Z",
    "value": 511.67
  }
}
```

**Backend Behavior**:
- Forward to all frontend clients in the session
- Optionally cache for new clients joining session

---

##### OVERLAY_MARKER
Agent emits a discrete event (signal, pattern detection, news sentiment, etc).

```json
{
  "type": "overlay_marker",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "pattern_detector",
  "schema": "event",
  "record": {
    "id": "pattern-bullish-engulfing-28497002",
    "ts": "2024-03-07T14:02:00Z",
    "event_type": "pattern_detected",
    "label": "Bullish Engulfing",
    "direction": "bullish",
    "confidence": 0.87,
    "metadata": {
      "pattern_bars": 2,
      "prior_candle_id": "SPY:1m:1709827260"
    }
  }
}
```

**Backend Behavior**:
- Forward to frontend for rendering as marker on chart
- Store with session timeline

---

##### RESYNC_REQUEST
Agent detects a sequence gap and requests replay of missed messages.

```json
{
  "type": "resync_request",
  "spec_version": "ACP-0.2.0",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "sub-123",
  "agent_id": "ema_20",
  "last_seq_received": 10040
}
```

**Backend Behavior**:
- Look up message buffer for this session
- Send `RESYNC_RESPONSE` with messages after `last_seq_received`
- If gap too large (buffer doesn't go back far enough), send full `HISTORY_PUSH` to reset agent

---

### Enhancement 2: Session Concept

**Change**: Introduce `session_id` as a first-class field in all messages.

#### Session Definition
A **session** represents:
- One frontend chart window
- One symbol + interval combination
- A distinct set of agent subscriptions
- An isolated canonical candle store
- Independent sequence numbering

#### Session Lifecycle

1. **Frontend creates session**:
   ```json
   {
     "type": "create_session",
     "ticker": "SPY",
     "interval": "1m",
     "timeframe_days": 7,
     "agent_ids": ["price_agent", "ema_20", "rsi_14", "pattern_detector"]
   }
   ```

2. **Backend assigns `session_id`**:
   ```json
   {
     "type": "session_created",
     "session_id": "550e8400-e29b-41d4-a716-446655440000",
     "timestamp": "2024-03-07T14:00:00Z"
   }
   ```

3. **Backend initializes session**:
   - Subscribe to price agent for symbol/interval
   - Fetch historical candles via price agent `/history`
   - Store candles in session's canonical store
   - Send `HISTORY_PUSH` to all overlay agents
   - Wait for `HISTORY_RESPONSE` from each agent
   - Mark session as `ready`

4. **Session runs**:
   - Price agent sends live OHLC updates → Backend ingests to canonical store
   - Backend pushes `TICK_UPDATE` / `CANDLE_CLOSED` to overlay agents
   - Overlay agents send `OVERLAY_UPDATE` / `OVERLAY_MARKER` back
   - Backend merges and forwards all to frontend clients in that session

5. **Frontend closes session**:
   ```json
   {
     "type": "close_session",
     "session_id": "550e8400-e29b-41d4-a716-446655440000"
   }
   ```

6. **Backend cleanup**:
   - Send `unsubscribe` to all agents for that session's subscriptions
   - Delete session state
   - Disconnect any remaining clients

#### Multi-Session Behavior
- One backend supports many concurrent sessions
- One agent connection is shared across multiple sessions (subscriptions are multiplexed)
- Example: Two chart windows both using `ema_20` agent:
  - Session A: `SPY @ 1m` → subscription_id = `sessionA:ema_20`
  - Session B: `QQQ @ 5m` → subscription_id = `sessionB:ema_20`
  - Agent receives distinct `HISTORY_PUSH` for each, maintains separate internal state per subscription

---

### Enhancement 3: Reliability & Sequence Tracking

**Change**: Formalize sequence gap detection and resync workflow.

#### Sequence Number Rules

1. **Backend assigns `seq`**:
   - Monotonically increasing per session
   - Starts at 0 when session created
   - Incremented for every message sent to agents or frontend

2. **Agents track `seq`**:
   - Store `last_seq_received` per subscription
   - On each message, check: `if msg.seq != last_seq_received + 1` → gap detected
   - Send `RESYNC_REQUEST` immediately

3. **Backend maintains replay buffer**:
   - Rolling deque of last ~100 messages per session
   - On `RESYNC_REQUEST`, replay from `last_seq_received + 1` forward
   - If gap too large, send `resync_failed` → agent resets with new `HISTORY_PUSH`

4. **Frontend tracks `seq`** (optional but recommended):
   - Same gap detection logic
   - Request resync from backend if needed

#### Resync Workflow

```
Agent:  Last received seq=10040
        Receives message with seq=10045
        
        → Send RESYNC_REQUEST(last_seq_received=10040)

Backend: Lookup buffer for session
         Messages 10041, 10042, 10043, 10044 found
         
         → Send RESYNC_RESPONSE(messages=[...])

Agent:  Process messages 10041-10044 in order
        Update last_seq_received=10044
        Continue with 10045
```

---

### Enhancement 4: Candle Lifecycle States

**Change**: Extend `bar_state` enum and document reconciliation semantics.

#### Bar State Enum
```typescript
type BarState = 
  | "partial"      // Candle forming (live ticks)
  | "final"        // Candle closed (streaming API close)
  | "reconciled"   // Corrected by REST API reconciliation
  | "eod_final"    // T+1 settlement complete (broker's final value)
```

#### State Transitions
```
partial → partial (many times, intrabar updates)
partial → final (on minute close)
final → reconciled (if REST API value differs from stream)
reconciled → eod_final (after settlement/EOD reconciliation)
```

#### Revision Semantics
- `rev` increments each time a candle with the same `id` is updated
- `partial` updates: rev increments freely
- `final`: typically `rev` stops unless correction occurs
- `reconciled`: `rev` increments again
- `eod_final`: terminal state, `rev` frozen

**Example Timeline**:
```
14:32:01 → {id: "SPY:1m:1709827920", rev: 0, bar_state: "partial", close: 512.45}
14:32:15 → {id: "SPY:1m:1709827920", rev: 1, bar_state: "partial", close: 512.48}
14:32:45 → {id: "SPY:1m:1709827920", rev: 2, bar_state: "partial", close: 512.42}
14:33:00 → {id: "SPY:1m:1709827920", rev: 3, bar_state: "final", close: 512.44}

[10 seconds later, REST API reconciliation runs]
14:33:10 → {id: "SPY:1m:1709827920", rev: 4, bar_state: "reconciled", close: 512.43}
```

**Agent Behavior**:
- Store candles keyed by `id`
- On receiving same `id` with higher `rev`, **upsert** (replace in place)
- Recompute indicators if correction affects calculation window
- Emit corrected overlay values if needed

---

### Enhancement 5: Agent Types & Roles

**Change**: Clarify agent taxonomy and data flow patterns.

#### Agent Type Taxonomy

##### 1. Price Agents (Data Sources)
- **Role**: Ingest broker API, produce OHLC candles
- **Examples**: Schwab price agent, IEX price agent
- **Protocol Flow**:
  - Backend subscribes to agent (standard ACP-0.1.0)
  - Agent streams `data` messages with `schema: "ohlc"`
  - Backend ingests into canonical session store
  - Backend does NOT push candles back to price agent

##### 2. Overlay Agents (Data Consumers & Producers)
- **Role**: Receive candles from backend, compute derived data, return overlays
- **Examples**: EMA/SMA indicator, RSI, pattern detector, news sentiment
- **Protocol Flow**:
  - Backend subscribes to agent (standard ACP-0.1.0)
  - Backend sends `HISTORY_PUSH` with canonical candles
  - Agent computes and returns `HISTORY_RESPONSE`
  - Backend streams `TICK_UPDATE` / `CANDLE_CLOSED` to agent
  - Agent returns `OVERLAY_UPDATE` / `OVERLAY_MARKER`

##### 3. Event Agents (External Data Sources)
- **Role**: Produce event markers independent of candles (news, earnings, social sentiment)
- **Examples**: News API agent, Twitter sentiment agent
- **Protocol Flow**:
  - Backend subscribes to agent (standard ACP-0.1.0)
  - Agent streams `data` messages with `schema: "event"`
  - Backend timestamps and merges into session timeline
  - Backend does NOT push candles to event agent

#### Agent Self-Declaration
Agents declare their type via `/metadata` endpoint:

```json
{
  "spec_version": "ACP-0.2.0",
  "agent_id": "ema_20",
  "agent_type": "overlay",  // NEW FIELD: "price" | "overlay" | "event"
  "data_dependency": "ohlc", // NEW FIELD: what schema does this agent need as input?
  "output_schema": "line"
}
```

**Backend Behavior**:
- Call `/metadata` before subscribing
- If `agent_type == "overlay"` and `data_dependency == "ohlc"`:
  - Backend will push candles to this agent
  - Expect `HISTORY_RESPONSE` and `OVERLAY_UPDATE` messages
- If `agent_type == "price"`:
  - Backend subscribes normally
  - Ingest candles to canonical store
  - Do NOT push candles back

---

### Enhancement 6: Backward Compatibility

**Goal**: ACP-0.2.0 is backward compatible with ACP-0.1.0 agents.

#### Compatibility Strategy

1. **Version Negotiation**:
   - All messages include `spec_version` field
   - Backend reads agent's `spec_version` from `/metadata`
   - If agent declares `ACP-0.1.0`: backend treats as data source only (no push)
   - If agent declares `ACP-0.2.0`: backend uses full bidirectional flow

2. **Message Type Handling**:
   - ACP-0.1.0 agents will ignore unknown message types (history_push, tick_update, etc)
   - ACP-0.1.0 agents emit `data`, `heartbeat`, `error` → backend handles normally
   - ACP-0.2.0 agents support all message types

3. **Session Field**:
   - `session_id` is optional for ACP-0.1.0 agents (backend adds internally)
   - ACP-0.2.0 agents MUST include `session_id` in all messages

4. **Graceful Degradation**:
   - If agent doesn't respond to `HISTORY_PUSH` within timeout (5s):
     - Backend logs warning
     - Continues without overlay from that agent
     - Frontend shows "agent not responding" status

---

## Implementation Checklist for ACP-0.2.0 Spec

### Core Specification Updates

- [ ] Add new message types to `schemas/messages.json`:
  - [ ] `history_push`
  - [ ] `tick_update`
  - [ ] `candle_closed`
  - [ ] `candle_correction`
  - [ ] `resync_response`
  - [ ] `resync_request`
  - [ ] `history_response`
  - [ ] `overlay_update`
  - [ ] `overlay_marker`

- [ ] Update `schemas/ohlc.json`:
  - [ ] Add `bar_state` enum: `["partial", "final", "reconciled", "eod_final"]`
  - [ ] Clarify `rev` semantics (monotonic upsert)
  - [ ] Add `seq` as required field (session sequence number)

- [ ] Add `schemas/session.json` (NEW):
  - [ ] Define session creation/closure messages
  - [ ] Define session_id format (UUIDv4)

- [ ] Update `spec/ACP.md`:
  - [ ] Add Section 15: Session Management
  - [ ] Add Section 16: Bidirectional Agent Flow
  - [ ] Add Section 17: Sequence Tracking and Gap Recovery
  - [ ] Add Section 18: Candle Lifecycle and Corrections
  - [ ] Add Section 19: Agent Type Taxonomy
  - [ ] Add Section 20: Backward Compatibility with ACP-0.1.0

- [ ] Update `/metadata` endpoint specification:
  - [ ] Add `agent_type` field (required): `"price" | "overlay" | "event"`
  - [ ] Add `data_dependency` field (optional): `"ohlc" | "events" | null`
  - [ ] Clarify that `config_schema` is required for overlay agents

### Documentation Updates

- [ ] Update `README.md`:
  - [ ] Mention ACP-0.2.0 as current version
  - [ ] Link to migration guide for 0.1.0 → 0.2.0

- [ ] Create `docs/MIGRATION_0.1_to_0.2.md`:
  - [ ] How to upgrade a price agent (minimal changes)
  - [ ] How to upgrade an overlay agent (add bidirectional support)
  - [ ] Backend implementation checklist

- [ ] Update `examples/basic_test_agent/`:
  - [ ] Implement ACP-0.2.0 overlay agent example
  - [ ] Show `HISTORY_PUSH` handling
  - [ ] Show `TICK_UPDATE` → `OVERLAY_UPDATE` flow

### Schema Validation

- [ ] Ensure all new message types validate against JSON Schema
- [ ] Add test cases for:
  - [ ] Session creation with multiple agent subscriptions
  - [ ] Sequence gap detection and resync
  - [ ] Candle correction with rev increment
  - [ ] Overlay response with 1000+ records

### Protocol Design Review

- [ ] Review with Odin backend team
- [ ] Validate message flow with sequence diagrams
- [ ] Load test: 1000 candles/min × 5 overlay agents × 10 sessions
- [ ] Ensure no race conditions in bidirectional push/response

---

## Example: Full Session Flow with ACP-0.2.0

### Scenario
Frontend user opens chart for **SPY @ 1-minute, 7-day history** with agents:
- `price_agent` (price source)
- `ema_20` (overlay: 20-period EMA)
- `rsi_14` (overlay: 14-period RSI)
- `pattern_detector` (overlay: bullish/bearish patterns)

### Flow

#### 1. Frontend → Backend: Create Session
```json
{
  "type": "create_session",
  "ticker": "SPY",
  "interval": "1m",
  "timeframe_days": 7,
  "agent_ids": ["price_agent", "ema_20", "rsi_14", "pattern_detector"]
}
```

#### 2. Backend → Frontend: Session Created
```json
{
  "type": "session_created",
  "session_id": "abcd-1234",
  "timestamp": "2024-03-07T14:00:00Z"
}
```

#### 3. Backend → Price Agent: Subscribe
```json
{
  "type": "subscribe",
  "spec_version": "ACP-0.2.0",
  "subscription_id": "abcd-1234:price_agent",
  "agent_id": "price_agent",
  "symbol": "SPY",
  "interval": "1m",
  "params": {"timeframe_days": 7}
}
```

#### 4. Backend → Price Agent: Fetch History (REST)
```http
GET /history?symbol=SPY&from=2024-02-29T14:00:00Z&to=2024-03-07T14:00:00Z&interval=1m
```

**Response**: 2,730 finalized OHLC bars

#### 5. Backend: Ingest to Canonical Store
- Store all 2,730 bars in `session.candles`
- Assign session `seq` starting at 0

#### 6. Backend → EMA Agent: Push History
```json
{
  "type": "history_push",
  "spec_version": "ACP-0.2.0",
  "session_id": "abcd-1234",
  "subscription_id": "abcd-1234:ema_20",
  "agent_id": "ema_20",
  "symbol": "SPY",
  "interval": "1m",
  "candles": [ /* 2,730 bars */ ],
  "count": 2730
}
```

#### 7. EMA Agent → Backend: History Response
```json
{
  "type": "history_response",
  "spec_version": "ACP-0.2.0",
  "session_id": "abcd-1234",
  "subscription_id": "abcd-1234:ema_20",
  "agent_id": "ema_20",
  "schema": "line",
  "overlays": [
    {"id": "ema-1", "ts": "2024-02-29T14:01:00Z", "value": 510.45},
    {"id": "ema-2", "ts": "2024-02-29T14:02:00Z", "value": 510.48},
    // ... 2,730 values
  ],
  "metadata": {"period": 20, "computation_time_ms": 120}
}
```

#### 8. Backend → Frontend: Merged History
```json
{
  "type": "session_ready",
  "session_id": "abcd-1234",
  "candles": [ /* 2,730 OHLC bars */ ],
  "overlays": {
    "ema_20": [ /* 2,730 line values */ ],
    "rsi_14": [ /* 2,730 line values */ ],
    "pattern_detector": [ /* 15 event markers */ ]
  }
}
```

#### 9. Live Stream: Price Agent → Backend
```json
{
  "type": "data",
  "spec_version": "ACP-0.2.0",
  "subscription_id": "abcd-1234:price_agent",
  "agent_id": "price_agent",
  "schema": "ohlc",
  "record": {
    "id": "SPY:1m:1709827980",
    "seq": 28497005,
    "rev": 0,
    "bar_state": "partial",
    "ts": "2024-03-07T14:33:00Z",
    "open": 512.59,
    "high": 512.60,
    "low": 512.58,
    "close": 512.59,
    "volume": 156
  }
}
```

#### 10. Backend: Ingest & Push to Overlays
- Upsert candle to `session.candles_by_id["SPY:1m:1709827980"]`
- Increment `session.seq` → 10001
- Push to overlay agents:

```json
{
  "type": "tick_update",
  "spec_version": "ACP-0.2.0",
  "session_id": "abcd-1234",
  "subscription_id": "abcd-1234:ema_20",
  "agent_id": "ema_20",
  "seq": 10001,
  "candle": { /* partial candle with rev=0 */ }
}
```

#### 11. EMA Agent → Backend: Overlay Update
```json
{
  "type": "overlay_update",
  "spec_version": "ACP-0.2.0",
  "session_id": "abcd-1234",
  "subscription_id": "abcd-1234:ema_20",
  "agent_id": "ema_20",
  "schema": "line",
  "record": {
    "id": "ema-2731",
    "ts": "2024-03-07T14:33:00Z",
    "value": 511.87
  }
}
```

#### 12. Backend → Frontend: Live Update
```json
{
  "type": "live_update",
  "session_id": "abcd-1234",
  "seq": 10001,
  "candle": { /* partial candle */ },
  "overlays": {
    "ema_20": {"ts": "2024-03-07T14:33:00Z", "value": 511.87},
    "rsi_14": {"ts": "2024-03-07T14:33:00Z", "value": 62.3}
  }
}
```

#### 13. Candle Close: Price Agent → Backend
```json
{
  "type": "data",
  "schema": "ohlc",
  "record": {
    "id": "SPY:1m:1709827980",
    "rev": 5,
    "bar_state": "final",
    "close": 512.61
  }
}
```

#### 14. Backend → Overlays: Candle Closed
```json
{
  "type": "candle_closed",
  "session_id": "abcd-1234",
  "seq": 10020,
  "candle": { /* final candle */ }
}
```

#### 15. Pattern Agent → Backend: Marker
```json
{
  "type": "overlay_marker",
  "session_id": "abcd-1234",
  "agent_id": "pattern_detector",
  "schema": "event",
  "record": {
    "id": "pattern-hammer-1709827980",
    "ts": "2024-03-07T14:33:00Z",
    "event_type": "pattern_detected",
    "label": "Hammer",
    "direction": "bullish",
    "confidence": 0.78
  }
}
```

#### 16. Backend → Frontend: Pattern Marker
Frontend renders green triangle above candle with tooltip "Hammer (78% confidence)"

---

## Summary of Changes for ACP-0.2.0

| Feature | ACP-0.1.0 | ACP-0.2.0 |
|---------|-----------|-----------|
| **Data Flow** | Unidirectional (agent → backend) | Bidirectional (backend ↔ agent) |
| **Session Support** | No session concept | `session_id` in all messages |
| **Candle Distribution** | Each agent sources own data | Backend owns canonical candles per session |
| **Overlay Agents** | Undefined | Defined protocol (HISTORY_PUSH → HISTORY_RESPONSE) |
| **Sequence Tracking** | `seq` exists but no gap handling | Gap detection + RESYNC_REQUEST/RESPONSE |
| **Candle Corrections** | No support | CANDLE_CORRECTION message type |
| **Agent Types** | Implicit | Explicit (`agent_type` in metadata) |
| **Message Types** | 6 types (subscribe, data, heartbeat, error, unsubscribe, reconfigure) | 15 types (adds 9 new bidirectional types) |
| **Backward Compat** | N/A | ACP-0.1.0 agents work as data sources |

---

## Next Steps for ACP Maintainer

1. **Review this specification** with Odin team for accuracy
2. **Draft ACP-0.2.0 spec document** in `spec/ACP-0.2.md`
3. **Update JSON schemas** in `schemas/` directory
4. **Create migration guide** for existing agents
5. **Implement reference overlay agent** in `examples/`
6. **Publish ACP-0.2.0** to `version/current.txt`
7. **Notify agent developers** of new capabilities

---

## Questions for Clarification

1. Should `HISTORY_PUSH` support incremental updates (e.g., only send new candles since last push)?
2. Should overlay agents cache candles locally, or rely on backend for re-push after disconnect?
3. What's the max recommended history size (e.g., 30 days @ 1-min = 11,700 candles)?
4. Should `CANDLE_CORRECTION` trigger automatic recomputation, or should agents decide?
5. Should session cleanup be explicit (`close_session`) or timeout-based (no activity for 5 min)?

---

**End of ACP Enhancement Specification**

This document is intended to guide the evolution of ACP from a unidirectional agent-sourced protocol to a bidirectional session-based architecture that supports overlay agents computing on canonical backend-owned candle data.
