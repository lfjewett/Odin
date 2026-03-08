# ACP (Agent Chart Protocol)

ACP is the protocol contract for Odin chart agents.

This repository is the authoritative source of truth for:
- agent metadata, history, and live protocol behavior
- schema validation rules
- version compatibility rules

## Current Version

- Protocol: `ACP-0.2.0`
- Version file: `version/current.txt`

## Why ACP Exists

Odin owns canonical candles per chart session and coordinates specialized agents (price, overlays, events). ACP provides a strict interface so independently built agents interoperate without ambiguity.

## Key Protocol Decisions (ACP-0.2.0)

- Transport model:
  - Backend calls price agent REST `/history` for snapshot backfill
  - Backend uses WebSocket for live and bidirectional session traffic
- Session model:
  - `session_id` required on ACP WebSocket protocol messages
  - single symbol per subscription
- Intervals (canonical enum): `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`
- Delivery: at-least-once
- Dedup:
  - non-OHLC: `(agent_id, id)`
  - OHLC stream: `(agent_id, id, rev)`
- OHLC lifecycle:
  - `partial` (forming)
  - `provisional_close` (stream close)
  - `session_reconciled` (REST reconciliation stage)
  - `final` (terminal by backend policy)

## Repository Layout

- `spec/ACP.md` — normative protocol spec
- `schemas/messages.json` — ACP WebSocket message envelopes
- `schemas/metadata.json` — metadata endpoint schema
- `schemas/ohlc.json` — OHLC lifecycle schema
- `schemas/session.json` — session lifecycle envelopes
- `schemas/line.json`, `event.json`, `band.json`, `histogram.json`, `forecast.json` — record schemas
- `version/current.txt` — current ACP version string
- `examples/basic_test_agent/` — runnable reference agents

## Quick Start (Reference Agents)

```bash
cd examples/basic_test_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn agent:app --reload --host 127.0.0.1 --port 8010
```

Price history example:

```bash
curl "http://127.0.0.1:8010/history?symbol=SPY&from=2026-03-01T10:00:00Z&to=2026-03-01T11:00:00Z&interval=1m"
```

Overlay example:

```bash
uvicorn overlay_agent:app --reload --host 127.0.0.1 --port 8011
```

## AI Agent Guardrail

Coding agents must read `AGENTS.md` before protocol changes and follow ACP spec/schema order strictly.
