# Odin UI-Only

This folder is a standalone frontend-only copy of the Odin UI, intended for bootstrapping a new project fork without the current backend.

## Run

```bash
cd frontend
npm install
npm run dev
```

## Backend integration TODOs

These files now use local placeholder behavior and contain explicit TODO markers for your new backend integration:

- `src/hooks/useAgentSubscriptions.ts`
  - Uses in-memory subscription CRUD instead of `/api/subscriptions`
- `src/history/fetchHistory.ts`
  - Generates mock OHLC history instead of `/api/history`
- `src/stream/useEventStream.ts`
  - Simulates live candle updates instead of WebSocket `/ws`
- `src/App.tsx`
  - Returns placeholder agent metadata in `handleInterrogateAgent`

Search for `TODO(new-project)` to find all replacement points quickly.
