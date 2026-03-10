- Everytime you edit the frontend app change the string at the top that says ODIN Market Workspace v0.xx to increment it to the next number

- For any UI/Backend state, event-stream, sync, subscription, or reconciliation change, consult and update `docs/ui-backend-sync-contract.md` in the same change.

## Chart Enhancements & Custom Primitives

For advanced chart modifications (time-region shading, overlays, etc.) in lightweight-charts, use the **custom primitive plugin pattern** rather than DOM overlays:

1. **Why**: Lightweight-charts recommends custom primitives for precise, performant visual elements that need to sync with chart state
2. **Pattern**: Implement `ISeriesPrimitive<Time>`, `IPrimitivePaneView`, and `IPrimitivePaneRenderer` classes
3. **Key APIs**:
   - `IPrimitivePaneRenderer.draw(target: CanvasRenderingTarget2D)` - for canvas rendering
   - `timeScale.timeToCoordinate(time)` - for converting chart time to pixel coordinates
   - `series.attachPrimitive(primitive)` - to attach to a series
4. **Reference**: See `ExtendedHoursShade` in ChartView.tsx for a working example. Check plugin-examples at [tradingview/lightweight-charts GitHub](https://github.com/tradingview/lightweight-charts/tree/master/plugin-examples) for additional patterns