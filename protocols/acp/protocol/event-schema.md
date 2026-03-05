# Event Schema Protocol Specification

## Overview
This document defines the canonical event contract for all messages flowing from backend to frontend over WebSocket.

## Core Event: Market Candle

Represents OHLCV (Open/High/Low/Close/Volume) data for a trading symbol at a point in time.

### Schema (Pydantic/JSON)

```python
class MarketCandle(BaseModel):
    event_id: str                    # Globally unique event identifier for dedup
    trace_id: str                    # Trace context for observability
    symbol: str                      # Trading symbol (e.g., "SPY", "AAPL", "BTC/USD")
    ts_event: datetime               # Producer event timestamp (when candle was generated)
    ts_ingest: datetime              # Backend ingest timestamp (when received by infra)
    open: float                      # Opening price
    high: float                      # Highest price in period
    low: float                       # Lowest price in period
    close: float                     # Closing price
    volume: int                      # Trading volume
    message_type: Literal["price"]   # Event type classification
    schema_version: str              # Semantic version for forward compatibility
```

### Field Definitions

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `event_id` | string | yes | Globally unique identifier, used for deduplication across retries and reconnects. Format is implementation-specific but must be unique per producer. | `"spy-0"`, `"spy-1"` |
| `trace_id` | string | yes | UUID v4 for distributed tracing. Links event through all systems (producer → backend → frontend). | `"550e8400-e29b-41d4-a716-446655440000"` |
| `symbol` | string | yes | Asset symbol traded on exchange. | `"SPY"`, `"AAPL"`, `"BTC/USD"` |
| `ts_event` | ISO 8601 datetime | yes | When the event occurred at source (producer). | `"2026-03-01T10:30:45.123456Z"` |
| `ts_ingest` | ISO 8601 datetime | yes | When backend received and processed the event. | `"2026-03-01T10:30:45.234567Z"` |
| `open` | float | yes | Opening price (OHLCV). | `450.00` |
| `high` | float | yes | High price in period. Must be ≥ `max(open, close)`. | `451.50` |
| `low` | float | yes | Low price in period. Must be ≤ `min(open, close)`. | `449.25` |
| `close` | float | yes | Closing price (OHLCV). | `450.75` |
| `volume` | integer | yes | Total volume traded in period. | `1000000` |
| `message_type` | enum | yes | Event classification. For candles, always `"price"`. | `"price"` |
| `schema_version` | string | yes | Semantic version of event schema. Enables breaking change detection. | `"1.0"` |

### Validation Rules

1. **Candle OHLC Ordering:**
   - `high >= max(open, close)`
   - `low <= min(open, close)`

2. **Positive Values:**
   - `open > 0`, `high > 0`, `low > 0`, `close > 0`
   - `volume >= 0` (can be zero in sparse markets)

3. **Unique IDs:**
   - `event_id` must be globally unique within a producer stream
   - `trace_id` should be UUIDv4

4. **Timestamps:**
   - Both `ts_event` and `ts_ingest` must be valid ISO 8601
   - `ts_ingest >= ts_event` (ingest time is after event time)

## Event Envelope

All WebSocket messages are wrapped in an `EventEnvelope` for extensibility.

```python
class EventEnvelope(BaseModel):
    event: Union[MarketCandle, None]  # The contained event or null for keepalive
```

### JSON Examples

**Market Candle Event:**
```json
{
  "event": {
    "event_id": "spy-0",
    "trace_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "SPY",
    "ts_event": "2026-03-01T10:30:45.123456Z",
    "ts_ingest": "2026-03-01T10:30:45.234567Z",
    "open": 450.00,
    "high": 451.50,
    "low": 449.25,
    "close": 450.75,
    "volume": 1000000,
    "message_type": "price",
    "schema_version": "1.0"
  }
}
```

**Keepalive/Heartbeat:**
```json
{
  "event": null
}
```

## Deduplication Strategy

### Producer-Side
- Each message must have a globally unique `event_id`.
- Producers **must not** retry sending with the same `event_id` if receiving system acknowledges.

### Client-Side (Browser)
- Maintain an in-memory set of seen `event_id`s.
- On receipt of event:
  1. If `event_id` in seen set → discard (duplicate)
  2. Otherwise → process event, add `event_id` to seen set
- For durability across browser restarts (Phase 1.5): persist seen `event_id`s to localStorage.

### Zero-Copy Dedup
- No server-side state needed.
- Client dedup is **idempotent**: processing same message twice with dedup yields same result as once.

## Schema Versioning

- Embed `schema_version` as semantic version (e.g., `"1.0"`, `"2.0"`).
- **Current:** `1.0`
- **Breaking Changes:** Increment major version and document migration.
- **Backward Compatibility:** Clients should accept higher minor versions (ignore new optional fields).

## Future Event Types

Phase 1 will introduce overlay events:

```python
class OverlayEvent(BaseModel):
    event_id: str
    trace_id: str
    symbol: str
    ts_event: datetime
    ts_ingest: datetime
    message_type: Literal["overlay"]  # New type
    overlay_type: str                  # e.g., "sentiment", "pattern", "regime"
    payload: dict                      # Overlay-specific data
    schema_version: str
```

All events will continue to use `EventEnvelope` for wrapping.
