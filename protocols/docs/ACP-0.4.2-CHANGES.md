# ACP-0.4.2 Changes Summary

## Release: March 12, 2026

ACP-0.4.2 is a **backward-compatible patch** focused on schema correctness and backend-authoritative area-zone conventions.

---

## What's New

### 1) Metadata schema enum fix for `area`

`area` is now included in both metadata output enum locations:

- top-level `outputs[].schema`
- indicator catalog `indicators[].outputs[].schema`

This aligns metadata discovery schemas with existing protocol/runtime support for `area` overlays.

### 2) Backend-authoritative area-zone guidance

For support/resistance and similar zone indicators:

- backend/agent should emit canonical zone records per candle timestamp
- numeric confidence should be emitted as `metadata.confidence`
- optional labels may be emitted as `metadata.label`
- optional render hints may be emitted under `metadata.render.*`

This keeps trading/export logic backend-complete while allowing UI-only display hints.

---

## Backward Compatibility

✅ **Fully backward compatible:**

- No required message fields changed
- No message envelope type removed
- Existing `ACP-0.4.0`/`ACP-0.4.1` payloads remain supported by compatibility logic
- Clients/agents that ignore added guidance continue to work unchanged

---

## Files Updated

- `protocols/version/current.txt` → `ACP-0.4.2`
- `protocols/spec/ACP.md` → spec header + 0.4.2 change section
- `protocols/schemas/metadata.json` → add `area` to output enums
- `protocols/schemas/messages.json` → allow `spec_version: ACP-0.4.2`

---

## Migration Notes

No migration required for existing integrations.

If you expose metadata for area outputs, validation now consistently accepts `schema: "area"` in both top-level outputs and indicator catalog outputs.
