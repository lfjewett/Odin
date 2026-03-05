# Basic ACP Test Agent

A minimal local ACP agent for protocol testing.

Implements:
- `GET /metadata` for agent configuration discovery and UI hints
- `GET /history` for finalized OHLC backfill
- `GET /health` for local health checks
- `WS /ws/live` for real-time partial/final OHLC streaming

## Behavior

- Supported symbol: `SPY`
- Supported intervals: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`
- Emits partial updates every second for the open bar (`bar_state=partial`)
- Emits terminal final update at bar close (`bar_state=final`)
- Uses stable candle `id` with monotonic `rev` per candle

## Run

```bash
cd examples/basic_test_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn agent:app --reload --host 127.0.0.1 --port 8010
```

## Metadata Example

```bash
curl "http://127.0.0.1:8010/metadata"
```

Returns agent configuration schema and visualization hints:
```json
{
  "spec_version": "ACP-0.1.0",
  "agent_id": "basic_price_agent",
  "agent_name": "Basic Price Agent",
  "agent_version": "0.1.0",
  "description": "Generates synthetic OHLC price data for testing ACP compliance",
  "config_schema": {},
  "output_schema": "ohlc",
  "overlay": {
    "kind": "ohlc",
    "panel": "price",
    "color": "#3b82f6",
    "legend": "basic_price_agent candles"
  }
}
```

## Backfill Example

```bash
curl "http://127.0.0.1:8010/history?symbol=SPY&from=2026-03-01T10:00:00Z&to=2026-03-01T11:00:00Z&interval=1m"
```

## WebSocket Example Messages

Subscribe:

```json
{
  "type": "subscribe",
  "spec_version": "ACP-0.1.0",
  "subscription_id": "sub-1",
  "agent_id": "basic_price_agent",
  "symbol": "SPY",
  "interval": "1m",
  "params": {}
}
```

Unsubscribe:

```json
{
  "type": "unsubscribe",
  "spec_version": "ACP-0.1.0",
  "subscription_id": "sub-1"
}
```
