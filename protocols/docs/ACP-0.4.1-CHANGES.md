# ACP-0.4.1 Changes Summary

## Release: March 10, 2026

ACP-0.4.1 is a **backward-compatible patch** that extends protocol capabilities for extensibility and visual features.

---

## What's New

### 1. Optional `metadata` Field (All Schemas)

Every record schema now supports an optional `metadata` object for agent-specific data that won't be rendered on the chart.

**Use Cases:**
- Hidden computation state (e.g., `time_since_crossover`, `offset_since_crossover`)
- Confidence scores and internal signals
- Trade decision context variables
- Up to 9+ additional data values per record

**Example (Line Record):**
```json
{
  "id": "fast-ma-1",
  "ts": "2026-03-10T14:30:00Z",
  "value": 52.0,
  "metadata": {
    "time_since_crossover": 45,
    "offset_since_crossover": 2.5,
    "signal_strength": 0.87,
    "rsi_reading": 65.3
  }
}
```

**Note:** All `additionalProperties` constraints now allow `metadata` field. Frontend will pass through metadata without rendering; trade engine can use it in rules.

---

### 2. New `area` Schema

Represents shaded regions between upper/lower bounds. Primary use case: heatmap indicators where fill color indicates direction (e.g., fast/slow MA crossover).

**Schema (area.json):**
```json
{
  "id": "ma-crossover-1",
  "ts": "2026-03-10T14:30:00Z",
  "upper": 52.0,
  "lower": 48.0,
  "metadata": {
    "fast_value": 52.0,
    "slow_value": 48.0,
    "crossover_signal": true
  }
}
```

**Rendering Rules (Frontend Implementation Expected):**
- Render shaded area between `upper` and `lower` lines
- **Color logic:** 
  - GREEN when `upper > lower` (fast > slow, bullish)
  - RED when `lower > upper` (slow > fast, bearish)
- Optional: transparency/opacity for visual layer stacking
- Both boundaries rendered as thin lines; area between filled with color

**Distinction from `band`:**
- `band` = Bollinger Bands, Keltner Channels (3 unshaded lines: upper, center, lower)
- `area` = Shaded heatmap (2 boundaries with directional fill)

---

## Schema Changes

All schemas now include optional `metadata` field:

| Schema | Updated | Purpose |
|--------|---------|---------|
| `ohlc.json` | ✅ | OHLCV data with hidden fields |
| `line.json` | ✅ | Single series with metadata support |
| `band.json` | ✅ | Bollinger Bands (3 lines) |
| `area.json` | ✅ **NEW** | Shaded region between bounds |
| `histogram.json` | ✅ | Histogram bars |
| `forecast.json` | ✅ | Predictions with metadata |
| `event.json` | ✅ | Events with metadata |

---

## Backward Compatibility

✅ **Fully backward compatible:**
- Agents that don't send `metadata` work with old frontends
- Frontends that ignore `metadata` work with new agents
- No breaking changes to required fields
- Existing subscriptions/configs unchanged

---

## Migration Guide for Indicator Builders

### Your Crossover Indicator

**Define 3 outputs in agent metadata:**

```json
{
  "outputs": [
    {
      "output_id": "fast_ma",
      "schema": "line",
      "label": "Fast MA",
      "is_primary": false
    },
    {
      "output_id": "slow_ma",
      "schema": "line",
      "label": "Slow MA",
      "is_primary": false
    },
    {
      "output_id": "ma_heatmap",
      "schema": "area",
      "label": "MA Crossover Heatmap",
      "is_primary": true
    }
  ]
}
```

**Send records per candle:**

```json
// Fast line
{
  "type": "overlay_update",
  "schema": "line",
  "record": {
    "id": "fast-1",
    "ts": "2026-03-10T14:30:00Z",
    "value": 52.0
  }
}

// Slow line
{
  "type": "overlay_update",
  "schema": "line",
  "record": {
    "id": "slow-1",
    "ts": "2026-03-10T14:30:00Z",
    "value": 48.0
  }
}

// Heatmap (hidden data goes in metadata)
{
  "type": "overlay_update",
  "schema": "area",
  "record": {
    "id": "heatmap-1",
    "ts": "2026-03-10T14:30:00Z",
    "upper": 52.0,
    "lower": 48.0,
    "metadata": {
      "time_since_crossover": 45,
      "offset_since_crossover": 2.5,
      "signal_strength": 0.87,
      "rsi_at_crossover": 65.3,
      "volume_ratio": 1.2
    }
  }
}
```

**Frontend handles:**
- Renders 2 charted lines (fast & slow)
- Renders shaded area with color logic
- Passes metadata through for trade engine access

**Trade engine has access to:**
- All metadata fields for rule evaluation
- Example rule: `CROSSOVER_SIGNAL AND TIME_SINCE_CROSSOVER < 10 AND SIGNAL_STRENGTH > 0.8`

---

## Frontend Implementation Notes

The frontend ChartView component needs enhancements:

1. **Area rendering**: Use lightweight-charts custom primitive pattern for filled regions
2. **Metadata passthrough**: Store metadata on overlay record objects (no UI changes needed)
3. **Color logic**: Check `upper > lower` to determine GREEN vs RED fill
4. **Optional transparency**: Consider `opacity` metadata field for layered visibility

---

## Frontend Trade Engine Access

Metadata is automatically available in trade decision context:

```typescript
// Backend passes metadata to trade engine for variables
const variables = [
  { name: "FAST_MA", type: "line", value: 52.0 },
  { name: "SLOW_MA", type: "line", value: 48.0 },
  { name: "TIME_SINCE_CROSSOVER", type: "metadata", value: 45 },
  { name: "OFFSET_SINCE_CROSSOVER", type: "metadata", value: 2.5 },
  { name: "SIGNAL_STRENGTH", type: "metadata", value: 0.87 }
];
```

Trade rules can reference metadata variables directly:
```
long_entry_rule = "FAST_MA > SLOW_MA AND TIME_SINCE_CROSSOVER < 10 AND SIGNAL_STRENGTH > 0.8"
```

---

## Test Cases

Verify ACP-0.4.1 compliance:

- [ ] Agents send `metadata` field, frontends ignore (no errors)
- [ ] Agents omit `metadata` field, frontends work (backward compat)
- [ ] Area schema renders with correct color based on upper/lower
- [ ] Metadata available in trade engine context
- [ ] Chunking still works with metadata payloads
- [ ] Dedup logic unchanged (still keyed on `(agent_id, id)`)

---

## Spec File Locations

- **Main Spec:** `/protocols/spec/ACP.md` (version 0.4.1)
- **Schemas:** `/protocols/schemas/`
  - `area.json` ← NEW
  - `line.json`, `band.json`, `area.json`, `histogram.json`, `forecast.json`, `event.json`, `ohlc.json` (all updated with metadata)

---

## Questions?

This extension maintains full backward compatibility. Existing agents and frontends continue to work unchanged. New agents can leverage `metadata` and `area` schema as needed.
