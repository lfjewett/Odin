# Basic ACP 0.2.0 Reference Agents

This folder includes two runnable ACP-0.2.0 examples:

- `agent.py` — price agent example (`agent_type=price`)
- `overlay_agent.py` — overlay agent example (`agent_type=overlay`)

## Price Agent Behavior

- Supported symbol: `SPY`
- Supported intervals: `1m`, `2m`, `3m`, `4m`, `5m`, `10m`, `15m`, `20m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `2d`, `1w`, `1M`
- Emits live OHLC updates with lifecycle:
  - `partial`
  - `provisional_close`
  - `session_reconciled` (simulated follow-up correction)
- Requires `session_id` in WebSocket protocol messages
- `GET /history` returns latest-snapshot candles including `bar_state` and `rev`

## Overlay Agent Behavior

- Accepts backend-pushed candle data via:
  - `history_push`
  - `tick_update`
  - `candle_closed`
  - `candle_correction`
- Returns:
  - `history_response`
  - `overlay_update`
  - `resync_request` on sequence gaps
- Computes a simple EMA-style line

## Run

```bash
cd examples/basic_test_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run price agent:

```bash
uvicorn agent:app --reload --host 127.0.0.1 --port 8010
```

Run overlay agent:

```bash
uvicorn overlay_agent:app --reload --host 127.0.0.1 --port 8011
```

## Metadata Example

```bash
curl "http://127.0.0.1:8010/metadata"
```

## Backfill Example

```bash
curl "http://127.0.0.1:8010/history?symbol=SPY&from=2026-03-01T10:00:00Z&to=2026-03-01T11:00:00Z&interval=1m"
```

## WebSocket Subscribe Example

```json
{
  "type": "subscribe",
  "spec_version": "ACP-0.2.0",
  "session_id": "session-1",
  "subscription_id": "session-1:price",
  "agent_id": "basic_price_agent",
  "symbol": "SPY",
  "interval": "1m",
  "params": {}
}
```

## WebSocket Unsubscribe Example

```json
{
  "type": "unsubscribe",
  "spec_version": "ACP-0.2.0",
  "session_id": "session-1",
  "subscription_id": "session-1:price",
  "agent_id": "basic_price_agent"
}
```
