# Odin Backend

FastAPI-based backend for the Odin trading platform.

## Setup

1. Create and activate a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On macOS/Linux
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running Locally

Start the backend server:

```bash
# From the backend/ directory with activated venv
python -app/main.py
```

Or use uvicorn directly:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will start on `http://localhost:8000`

## Endpoints

- `GET /` - Service info and health check
- `GET /health` - Detailed health status
- `WS /ws` - WebSocket endpoint for frontend connections

## Development

The backend is configured with auto-reload enabled during development. Any changes to Python files will automatically restart the server.

## Next Steps

This MVP backend currently:
- ✅ Accepts WebSocket connections from the frontend
- ✅ Sends connection confirmation and heartbeats
- ✅ Provides connection status visibility to the UI

Future enhancements:
- Subscribe to ACP agents via their WebSocket endpoints
- Request historical data from agent REST endpoints
- Merge and normalize agent streams
- Forward unified events to frontend
- Agent discovery and management
- Subscription lifecycle management
