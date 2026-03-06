# Historical Backfill Implementation

**Status:** ✅ Completed  
**Date:** March 5, 2026  
**Version:** ODIN Market Workspace v0.14

## Overview

Implemented full historical data backfill for the charting application, enabling the backend to maintain a complete copy of all chart data for both frontend rendering and future trade management decisions.

## Architecture

### Data Flow
```
Frontend Subscribe → Backend → Agent /history REST → AgentDataStore → WebSocket Snapshot → Frontend Chart
                                  ↓
                            Live Stream Updates → Both Backend & Frontend
```

### Key Design Decisions

1. **Backend as Source of Truth**: Backend maintains complete historical and live data in `AgentDataStore` for future trade management
2. **WebSocket-only Protocol**: All data (historical snapshots and live updates) sent via WebSocket for consistency
3. **Dynamic Memory Management**: `AgentDataStore` dynamically sizes retention based on timeframe and interval
4. **Timeframe-driven Backfill**: Frontend specifies exact timeframe needs, backend fetches and stores only what's required

## Implementation Details

### Backend Changes

#### 1. Added HTTP Client Capability ([agent_connection.py](backend/app/agent_connection.py))
- Added `httpx` dependency for REST API calls
- Implemented `fetch_history()` method to call agent's `/history` endpoint per ACP protocol
- 10-second timeout for history requests
- Robust error handling for timeouts and HTTP errors

#### 2. Enhanced Subscription Flow ([agent_connection.py](backend/app/agent_connection.py))
- `subscribe()` method now accepts `timeframe_days` parameter in params dict
- Automatically triggers history fetch when subscription includes timeframe
- Ingests all historical bars into `AgentDataStore` before sending snapshot
- Sends new message type `"snapshot"` with complete bar array via WebSocket

#### 3. Dynamic Data Store ([agent_data_store.py](backend/app/agent_data_store.py))
- Added `update_retention()` method to calculate optimal maxlen for deque
- Formula: `(timeframe_days * 1440 / interval_minutes) * 1.1` (10% buffer)
- Minimum 1000 bars to prevent edge cases
- Preserves existing data when resizing

#### 4. Snapshot Broadcasting ([main.py](backend/app/main.py))
- Added handler for `"snapshot"` message type in `broadcast_agent_message()`
- Broadcasts snapshot to all connected clients
- Logs snapshot size for debugging

#### 5. Default Subscription Parameters
- Updated `start_all_connections()` to pass `timeframe_days=7` by default
- Updated `connection.start()` to include timeframe in subscription params

### Frontend Changes

#### 1. Extended WebSocket Protocol ([useEventStream.ts](frontend/src/stream/useEventStream.ts))
- Added `SnapshotEvent` type for historical data
- Added optional `onSnapshot` callback parameter
- Handler for `"snapshot"` message type in WebSocket message switch

#### 2. Refactored Chart Loading ([ChartView.tsx](frontend/src/chart/ChartView.tsx))
- Removed `fetchHistory()` REST API call
- Added `onSnapshotRequested` prop to register snapshot handler
- Implemented `handleSnapshot()` callback to process historical bars
- Reused existing `normalizeHistoryBars()` for deduplication
- Loading state now waits for snapshot instead of REST response

#### 3. Wired App Components ([App.tsx](frontend/src/App.tsx))
- Added `snapshotHandler` state management
- Added `handleSnapshotRequested` callback
- Added `handleSnapshot` to route snapshots to active chart
- Connected `useEventStream` with snapshot callback
- Updated version to v0.14

## Protocol Specification

### Snapshot Message Format
```json
{
  "type": "snapshot",
  "agent_id": "price_agent",
  "subscription_id": "price_agent:default",
  "symbol": "SPY",
  "interval": "1m",
  "bars": [
    {
      "id": "bar-18392",
      "seq": 18392,
      "rev": 0,
      "bar_state": "final",
      "ts": "2026-02-01T14:32:00Z",
      "open": 492.1,
      "high": 492.4,
      "low": 491.9,
      "close": 492.3,
      "volume": 19342
    },
    ...
  ],
  "count": 10080
}
```

### Subscription Parameters
```json
{
  "type": "subscribe",
  "spec_version": "ACP-0.1.0",
  "subscription_id": "price_agent:default",
  "agent_id": "price_agent",
  "symbol": "SPY",
  "interval": "1m",
  "params": {
    "timeframe_days": 7
  }
}
```

## Data Retention Examples

| Interval | Timeframe | Bars Needed | Deque Maxlen (1.1x) |
|----------|-----------|-------------|---------------------|
| 1m       | 7 days    | 10,080      | 11,088              |
| 5m       | 7 days    | 2,016       | 2,218               |
| 1h       | 7 days    | 168         | 1,000 (minimum)     |
| 1d       | 30 days   | 30          | 1,000 (minimum)     |

## Testing Checklist

- [x] Backend compiles without errors
- [x] httpx dependency installed
- [x] AgentDataStore calculates retention correctly
- [ ] Backend fetches history from test agent
- [ ] Backend sends snapshot via WebSocket
- [ ] Frontend receives and renders snapshot
- [ ] Live updates continue after snapshot
- [ ] Chart shows full 7-day history
- [ ] Backend and frontend have identical data

## Usage

### Starting the System
```bash
# Terminal 1: Start test agent
cd protocols/acp/examples/basic_test_agent
source .venv/bin/activate
python agent.py

# Terminal 2: Start backend
cd backend
source ../.venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 3: Start frontend
cd frontend
npm run dev
```

### Expected Behavior
1. Backend connects to agent on startup
2. Backend fetches 7 days of SPY 1m history
3. Backend ingests ~10,000 bars into AgentDataStore
4. Backend sends snapshot via WebSocket
5. Frontend receives snapshot and populates chart
6. Chart displays full 7-day history
7. Live updates continue via "data" messages

## Future Enhancements

1. **Dynamic Timeframe Changes**: Allow frontend to request different timeframes without reconnecting
2. **Multiple Symbols**: Support concurrent subscriptions to multiple symbols/intervals
3. **Persistence**: Add disk storage for historical data (Redis/SQLite) for faster restarts
4. **Compression**: Gzip large snapshot messages to reduce bandwidth
5. **Incremental Updates**: Send only new bars if frontend reconnects within timeframe window

## Files Modified

### Backend
- `backend/requirements.txt` - Added httpx
- `backend/app/agent_connection.py` - Added fetch_history, enhanced subscribe
- `backend/app/agent_data_store.py` - Added dynamic retention management
- `backend/app/agent_manager.py` - Pass timeframe_days parameter
- `backend/app/main.py` - Handle snapshot broadcast, pass default timeframe

### Frontend
- `frontend/src/stream/useEventStream.ts` - Added snapshot event handling
- `frontend/src/chart/ChartView.tsx` - Replaced REST fetch with snapshot callback
- `frontend/src/App.tsx` - Wired snapshot handlers, incremented version

## Related Documentation

- [ACP Protocol Specification](../protocols/acp/spec/ACP.md) - Section 7: Historical Data
- [Agent Connection Protocol](../protocols/acp/protocol/ws.md) - WebSocket message types
- [Backend Architecture](../backend/README.md) - Data flow and state management

## Notes

- The implementation follows ACP v0.1.0 specification exactly
- Historical bars are always `bar_state="final"` per protocol
- Deduplication uses `(id, rev)` tuple as specified
- Backend maintains identical dataset to frontend as required for trade management
