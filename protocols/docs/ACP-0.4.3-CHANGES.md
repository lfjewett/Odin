# ACP-0.4.3 Changes Summary

## Release: March 12, 2026

ACP-0.4.3 tightens overlay output identity requirements to make multi-output overlays deterministic for rendering, export, and strategy evaluation.

---

## What's New

### 1) `output_id` required on all non-OHLC overlay records

The following record schemas now require `output_id` on each record:

- `line`
- `band`
- `area`
- `histogram`
- `forecast`
- `event`

This makes every emitted overlay record unambiguously attributable to an output stream.

### 2) Clarified normative mapping to metadata outputs

For multi-output indicators, each record `output_id` must match an output descriptor declared in:

- top-level metadata `outputs[]`, or
- indicator catalog `indicators[].outputs[]`

This removes ambiguity when multiple zones/series share the same timestamp.

### 3) Message envelope schema version update

`messages.json` now accepts `spec_version: ACP-0.4.3`.

---

## Compatibility Notes

- ACP-0.4.3 introduces stricter validation for record payloads.
- Agents that previously omitted per-record `output_id` must be updated.
- Backend implementations may continue to accept older specs for transition, but 0.4.3 payloads should be strictly validated.

---

## Files Updated

- `protocols/version/current.txt` → `ACP-0.4.3`
- `protocols/spec/ACP.md` → spec header + 0.4.3 change section + normative rule
- `protocols/schemas/messages.json` → add `ACP-0.4.3` to `spec_version` enum
- `protocols/schemas/line.json` → require `output_id`
- `protocols/schemas/band.json` → require `output_id`
- `protocols/schemas/area.json` → require `output_id`
- `protocols/schemas/histogram.json` → require `output_id`
- `protocols/schemas/forecast.json` → require `output_id`
- `protocols/schemas/event.json` → require `output_id`

---

## Migration Guidance

1. Update agent metadata outputs so all emitted record streams have stable output IDs.
2. Ensure every `history_response.overlays[]` and `overlay_update.record` includes non-empty `output_id`.
3. For multi-zone indicators (e.g., S/R), use deterministic IDs such as `sr_0`, `sr_1`, ... and keep them stable through session lifetime.
