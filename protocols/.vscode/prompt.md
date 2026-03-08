# Agent Chart Protocol (ACP)
Version: 0.1.0  
Status: Draft

## 1. Overview

This protocol defines how data-producing agents communicate with a charting UI and with each other.  
All chart data (including price candles) is produced by agents.

Agents may provide:
- Price data
- Mathematical indicators
- External data (news, options, macro)
- AI-generated analysis (patterns, predictions)

All agents follow a uniform interface for:
- Registration
- Historical backfill
- Real-time streaming
- Heartbeats
- Subscription lifecycle
- Rendering hints
- Test data generation

The UI is a pure consumer of agent data and never computes indicators itself.

---

## 2. Core Concepts

### 2.1 Agents
An agent is a service that:
- Accepts subscriptions
- Emits time-indexed data
- Declares how its output should be visualized

Agents are independent and stateless per subscription.

### 2.2 Schemas
Supported schemas:
- `ohlc` — candlestick bars
- `line` — single numeric series
- `event` — point-in-time events
- `band` — upper/lower ranges
- `histogram`
- `forecast`

All records MUST include:
- `id`
- `ts` (UTC ISO 8601)
- schema-specific fields

---

## 3. Registration

On startup, an agent sends:

```json
{
  "type": "register",
  "agent_id": "news_sentiment_v1",
  "version": "1.0.0",
  "schemas": ["event"],
  "supports": ["historical", "streaming"],
  "overlay": {
    "kind": "event",
    "panel": "price",
    "color": "by_sentiment",
    "icon": "dot"
  }
}
4. Subscription Lifecycle
4.1 Subscribe

{
  "type": "subscribe",
  "subscription_id": "sub-123",
  "agent_id": "sma_20",
  "symbol": "SPY",
  "params": {
    "lookback_days": 7,
    "interval": "1m"
  }
}
4.2 Unsubscribe

{
  "type": "unsubscribe",
  "subscription_id": "sub-123"
}
4.3 Reconfigure (Optional)

{
  "type": "reconfigure",
  "subscription_id": "sub-123",
  "new_params": {
    "lookback_days": 30,
    "interval": "5m"
  }
}

Reconfigure MUST behave identically to:
unsubscribe
fresh subscribe

5. Historical Backfill
Agents MUST support:
Copy code

GET /history?symbol=SPY&from=2026-01-01&to=2026-02-01&interval=1m
Response:

{
  "agent_id": "price_agent",
  "schema": "ohlc",
  "data": [
    {
      "id": "bar-18392",
      "seq": 18392,
      "ts": "2026-02-01T14:32:00Z",
      "open": 492.1,
      "high": 492.4,
      "low": 491.9,
      "close": 492.3,
      "volume": 19342
    }
  ]
}

Rules:
id must be stable
seq must be monotonic
missing seq implies gap

6. Streaming Data

{
  "type": "data",
  "subscription_id": "sub-123",
  "agent_id": "sma_20",
  "schema": "line",
  "record": {
    "id": "pt-9382",
    "ts": "2026-02-01T14:33:00Z",
    "value": 492.91
  }
}

Deduplication key:
(agent_id + id)

7. Heartbeats

{
  "type": "heartbeat",
  "agent_id": "sma_20",
  "subscription_id": "sub-123",
  "status": "ok",
  "uptime": 3923,
  "last_event_ts": "2026-02-01T14:32:00Z"
}

UI:
marks overlay stale if heartbeat missing

8. Overlay Hints

{
  "overlay": {
    "kind": "line",
    "panel": "separate",
    "y_range": [-1, 1],
    "color": "purple",
    "legend": "News Sentiment"
  }
}
Kinds:
line
event
band
heatmap
forecast
Panels:
price
separate

9. Price Agent Rules
Price agent:
is canonical OHLC source
emits finalized bars only
assigns seq
stores history
supports replay

Indicator agents:
subscribe to price agent
detect missing seq
request backfill
recompute deterministically

10. UI Rules
UI:
never computes indicators
subscribes to agents
purges all subscriptions on:
symbol change
timeframe change
interval change

UI treats chart as:

f(agents, params) => visualization
11. Versioning
Spec version: ACP-0.2.0
Agents must declare supported spec version:
{
  "spec_version": "ACP-0.2.0"
}

Breaking changes require bump:
0.x.y = non-breaking
x.0.0 = breaking
12. Failure Handling
gap detected → request backfill
duplicate id → ignore
stale heartbeat → mark overlay inactive

13. Goals
Deterministic replay
Agent independence
Pluggable data sources
Unified visualization
AI-compatible architecture

Create something like:

Inside:
/spec ACP.md /schemas ohlc.json line.json event.json /version current.txt

Then:

- Agents depend on it as:
  - a git submodule  
  - or a pinned commit  
  - or a package (later)
