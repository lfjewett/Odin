- Everytime you edit the frontend app change the string at the top that says ODIN Market Workspace v0.xx to increment it to the next number

- For any UI/Backend state, event-stream, sync, subscription, or reconciliation change, consult and update `docs/ui-backend-sync-contract.md` in the same change.

## AgentConfigModal Form Reset Bug — DO NOT REINTRODUCE

The form reset in `AgentConfigModal` has been reintroduced 3+ times. Here is the root cause and fix:

**Root cause**: `useAgentSubscriptions` runs a background `setInterval` that calls `refresh(true)` every 5 seconds. This calls `setSubscriptions(agents)` with a fresh array of new object references. The `subscription` prop (passed from App.tsx) therefore changes reference every 5s even though the data is identical. This causes:
1. `editableFields` useMemo (which depends on the full `subscription` object) to recompute → new array reference
2. The form-init `useEffect` fires because `editableFields` was in its dependency array
3. All form fields reset to whatever the backend returned — erasing the user's in-progress edits

**The fix**: The form-init `useEffect` dependency array MUST be **`[isOpen, subscription?.id]` only** — NOT `editableFields` or `inferredIndicator`. Both of those are derived synchronously from `subscription` at render time and are valid to read from the closure when the effect actually runs (i.e., when the modal opens or switches to a new subscription ID). Do NOT add `editableFields`, `inferredIndicator`, or the full `subscription` object back to that dep array. The eslint-disable comment explains this intentional omission.

## UI-Managed Indicator Config Keys — Backend Persistence

Any config key that the UI owns (not declared in the agent's `params_schema`) must be explicitly included in `UI_MANAGED_INDICATOR_CONFIG_KEYS` in `backend/app/models.py` or it will be silently stripped by `sanitize_indicator_params` (which only keeps schema-declared keys) and never saved.

**Current UI-managed keys**: `line_color`, `visible`, `aggregation_interval`, `force_subgraph`

**Rule**: If you add a new UI-only config key (e.g., a display toggle, color, or rendering override):
1. Add it to `UI_MANAGED_INDICATOR_CONFIG_KEYS` in `models.py`
2. The POST `/api/agents` handler already uses `_ui_keys` dict comprehension to forward all of these from `request.params` — no change needed there
3. The PATCH `/api/agents/{id}` handler uses `_existing_ui_keys` (preserve) + `_request_ui_keys` (override) — no change needed there either

The `normalize_indicator_config` function uses `allowed_keys = set(params_schema.keys()) | UI_MANAGED_INDICATOR_CONFIG_KEYS` which means any key in `UI_MANAGED_INDICATOR_CONFIG_KEYS` automatically passes through the normalization step.

## Chart Enhancements & Custom Primitives

For advanced chart modifications (time-region shading, overlays, etc.) in lightweight-charts, use the **custom primitive plugin pattern** rather than DOM overlays:

1. **Why**: Lightweight-charts recommends custom primitives for precise, performant visual elements that need to sync with chart state
2. **Pattern**: Implement `ISeriesPrimitive<Time>`, `IPrimitivePaneView`, and `IPrimitivePaneRenderer` classes
3. **Key APIs**:
   - `IPrimitivePaneRenderer.draw(target: CanvasRenderingTarget2D)` - for canvas rendering
   - `timeScale.timeToCoordinate(time)` - for converting chart time to pixel coordinates
   - `series.attachPrimitive(primitive)` - to attach to a series
4. **Reference**: See `ExtendedHoursShade` in ChartView.tsx for a working example. Check plugin-examples at [tradingview/lightweight-charts GitHub](https://github.com/tradingview/lightweight-charts/tree/master/plugin-examples) for additional patterns