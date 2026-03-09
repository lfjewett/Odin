# ACP Migration Guide: v0.2.0 -> v0.3.0

This document is the forward-only migration checklist for Odin price agents and indicator agents.

## Breaking Changes Summary

- Protocol version is now `ACP-0.3.0`.
- Agent type `overlay` is renamed to `indicator`.
- `/history` is required only for `agent_type=price`.
- Metadata no longer carries UI rendering ownership (`overlay` block removed).
- Metadata now requires typed `outputs[]`.
- Indicator agents must expose an indicator catalog in `indicators[]`.
- Indicator selection is metadata-driven (`indicator_id` in subscribe/reconfigure), not URL path routing.

## 1) Price Agent Migration

### Required changes

1. Update metadata:
   - `spec_version: ACP-0.3.0`
   - `agent_type: price`
   - Include `outputs[]` with at least one primary OHLC output.
2. Keep `/history` endpoint and behavior unchanged semantically:
   - Ascending candles
   - Inclusive `from`, exclusive `to`
   - Mutable candles with monotonic `rev`
   - Valid `bar_state`
3. Ensure WS messages use `spec_version: ACP-0.3.0`.
4. Keep canonical intervals: `1m`, `2m`, `3m`, `4m`, `5m`, `10m`, `15m`, `20m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `2d`, `1w`, `1M`.

### Price metadata example

```json
{
  "spec_version": "ACP-0.3.0",
  "agent_id": "price_agent",
  "agent_name": "Price Data",
  "agent_version": "1.1.0",
  "description": "Live OHLCV candle data",
  "agent_type": "price",
  "data_dependency": "none",
  "config_schema": {
    "ticker": { "type": "string", "description": "Chart symbol", "required": true },
    "interval": { "type": "string", "description": "Candle interval", "required": true }
  },
  "outputs": [
    {
      "output_id": "price.ohlc",
      "schema": "ohlc",
      "label": "Price Candles",
      "is_primary": true
    }
  ]
}
```

## 2) Indicator Agent Migration

### Required changes

1. Rename role and version:
   - `spec_version: ACP-0.3.0`
   - `agent_type: indicator`
2. Remove old metadata fields:
   - Remove `output_schema`
   - Remove `overlay`
3. Add typed outputs and indicator catalog:
   - Top-level `outputs[]` for aggregate capability
   - Required `indicators[]` catalog for discoverability
4. Support `indicator_id` in subscription/reconfigure payloads.
5. `/history` is optional for indicator agents.

### Indicator metadata example

```json
{
  "spec_version": "ACP-0.3.0",
  "agent_id": "indicator_agent",
  "agent_name": "Indicator Engine",
  "agent_version": "1.1.0",
  "description": "Computes multiple indicators from canonical candles",
  "agent_type": "indicator",
  "data_dependency": "ohlc",
  "config_schema": {},
  "outputs": [
    { "output_id": "line", "schema": "line", "label": "Line", "is_primary": true },
    { "output_id": "event", "schema": "event", "label": "Event", "is_primary": false }
  ],
  "indicators": [
    {
      "indicator_id": "ema",
      "name": "Exponential Moving Average",
      "description": "EMA over close",
      "params_schema": {
        "period": { "type": "integer", "description": "EMA period", "required": true, "default": 9, "min": 1 }
      },
      "outputs": [
        { "output_id": "ema.line", "schema": "line", "label": "EMA", "is_primary": true }
      ]
    },
    {
      "indicator_id": "macd",
      "name": "MACD",
      "description": "MACD with crossover events",
      "params_schema": {
        "fast": { "type": "integer", "description": "Fast EMA", "required": true, "default": 12 },
        "slow": { "type": "integer", "description": "Slow EMA", "required": true, "default": 26 },
        "signal": { "type": "integer", "description": "Signal EMA", "required": true, "default": 9 }
      },
      "outputs": [
        { "output_id": "macd.line", "schema": "line", "label": "MACD", "is_primary": true },
        { "output_id": "macd.signal", "schema": "line", "label": "Signal", "is_primary": false },
        { "output_id": "macd.cross", "schema": "event", "label": "Crossovers", "is_primary": false }
      ]
    }
  ]
}
```

## 3) Discovery and Add-Indicator Flow Contract

- User enters base URL only, e.g. `http://localhost:8020`.
- Odin backend calls `{base_url}/metadata`.
- Odin presents `indicators[]` to user.
- User selects one indicator + params.
- Odin creates one subscription per indicator selection.
- Odin fans out one canonical candle stream to all indicator subscriptions.

## 4) Validation Checklist

- [ ] Metadata `spec_version` is `ACP-0.3.0`.
- [ ] `agent_type` is one of `price|indicator|event`.
- [ ] `outputs[]` exists and each output has `output_id|schema|label|is_primary`.
- [ ] Indicator agents include non-empty `indicators[]`.
- [ ] Price agents still implement `/history`; indicator agents are not required to.
- [ ] WS subscribe/reconfigure can accept `indicator_id`.
- [ ] All outgoing protocol messages use `spec_version: ACP-0.3.0`.
