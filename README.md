# Odin Trading Platform

A real-time, TradingView-inspired trading UI platform that unifies market candles (OHLCV) and AI/agent overlay events on a single timeline. Built with Python/FastAPI backend and React frontend.

## Quick Start

**Terminal 2: Backend**
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001
```

**Terminal 3: Frontend Dev Server**
```bash
cd frontend
npm install
npm run dev
```

The frontend dev server runs on **http://localhost:8000** and proxies WebSocket/API requests to the backend on port 8001.


## Features (Phase 2)

✅ ACP price-agent stream ingestion (`ws://localhost:8010/ws/live`)  
✅ Fixed Phase 1 subscription defaults (`SPY`, `1m`)  
✅ ACP envelope forwarding to browser (`data`, `heartbeat`, `error`)  
✅ OHLC dedupe semantics by `(agent_id, id, rev)` in backend  
✅ TradingView Lightweight Charts rendering  
✅ Structured JSON logging + in-process reliability metrics  
✅ ACP schema validation at ingress for metadata and live records  
✅ CI backend smoke test workflow (push/PR)  

## Testing WebSocket Reconnect

1. Open `http://localhost:8000` and observe candles rendering
2. Press **F5** to reload the page
3. Chart should reconnect and continue receiving new candles (no visible loss)


## Architecture

```
┌──────────────────┐
│  Browser         │
│  (React + Chart) │    Port 8000 (Vite dev server)
└────────┬─────────┘
         │
    WebSocket (/ws) + API (/api)
         │ (proxied by Vite)
         ▼
┌──────────────────────────┐
│  FastAPI Backend         │    Port 8001
│  - ACP client (/ws/live) │
│  - WebSocket router      │
│  - Static file serving   │
└──────────┬───────────────┘
           │
      WebSocket (/ws/live)
           │
           ▼
┌──────────────────────────┐
│  Price Data Agent        │    Port 8010
│  (ACP 0.2.0)             │
└──────────────────────────┘
```

## Development

### Backend Development

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001 --log-level info
```

Run tests:
```bash
python -m pytest tests/ -v
```

### Frontend Development

```bash
cd frontend
npm install
npm run dev  # Start Vite dev server on http://localhost:8000
```

The dev server proxies WebSocket (`/ws`) and API (`/api`) requests to the backend at `localhost:8001`.

Build for production:
```bash
npm run build  # Output to dist/
```

## Documentation

- **Architecture:** See [architecture.md](./architecture.md) for high-level design
- **Phase Plan:** See [phase-02-plan.md](./phase-02-plan.md) for current reliability scope
- **Event Protocol:** See [docs/protocol/event-schema.md](./docs/protocol/event-schema.md)
- **WebSocket Spec:** See [docs/protocol/ws.md](./docs/protocol/ws.md)
- **ADRs:** See [docs/adr/](./docs/adr/) for architecture decisions

## Roadmap

- **Phase 0:** ✅ Protocol + Hello Stream
- **Phase 1:** ✅ ACP Price Agent Integration
- **Phase 1a:** ✅ User Config Persistence (SQLite)
- **Phase 2:** 🚧 Developer Reliability (logging, testing, chaos)
- **Phase 3:** First Real Broker Adapter
- **Phase 4:** Historical Data Support
- **Phase 5:** Durability Upgrade (Postgres/Timescale)

## Health Check

```bash
curl http://localhost:8001/health
```

Returns:
```json
{
     "status": "ok|degraded",
     "active_connections": 1,
     "upstream": {"connected": true},
     "metrics": {
          "acp_ingress_messages_total|message_type=data": 120,
          "acp_messages_forwarded_total|message_type=data,schema=ohlc": 118,
          "acp_messages_dropped_total|reason=duplicate_ohlc": 2
     }
}
```

## Phase 2 Reliability Runbook

### Chaos test toggles (simulator)

Set these environment variables when you want to test disorder scenarios:

- `ODIN_SIM_CHAOS_DROP_RATE` (0.0 - 1.0): Probability an event is dropped
- `ODIN_SIM_CHAOS_REORDER_RATE` (0.0 - 1.0): Probability an event is delayed and emitted after the next event
- `ODIN_SIM_CHAOS_DUPLICATE_RATE` (0.0 - 1.0): Probability an event is emitted twice
- `ODIN_SIM_CHAOS_SEED` (integer): Optional deterministic seed for reproducible chaos runs

Example:

```bash
export ODIN_SIM_CHAOS_DROP_RATE=0.05
export ODIN_SIM_CHAOS_REORDER_RATE=0.10
export ODIN_SIM_CHAOS_DUPLICATE_RATE=0.15
export ODIN_SIM_CHAOS_SEED=42
```

### Health metrics interpretation

Use `/health` to inspect reliability counters during test runs:

- `acp_ingress_messages_total`: Total upstream envelopes seen
- `acp_messages_forwarded_total`: Messages forwarded to browser clients
- `acp_messages_dropped_total`: Dropped messages (schema validation, duplicate OHLC, unsupported type/schema)
- `acp_ohlc_dedup_hits_total`: Duplicate OHLC revisions filtered by dedupe
- `acp_broadcast_failures_total`: Fanout loop failures

### CI checks

GitHub Actions workflow: `.github/workflows/ci.yml`

- Installs backend dependencies
- Runs Python smoke compile (`compileall`)
- Runs backend test suite (`pytest backend/tests -v --tb=short`)

## Logs

View structured JSON logs:
```bash
make logs  # Or: docker-compose logs -f odin
```

## Troubleshooting

### Frontend dist not found
Ensure you've built the frontend:
```bash
cd frontend && npm install && npm run dev
```

### WebSocket connection refused
1. Verify backend is running: `curl http://localhost:8001/health`

## License

TBD

## Contact

Questions or feedback? Open an issue or reach out to the team.
    agent_url: "http://host.docker.internal:8010"
