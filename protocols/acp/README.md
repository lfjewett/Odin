# ACP (Agent Chart Protocol)

ACP is the protocol contract for Odin chart agents.

This repository is the authoritative source of truth for:
- agent/backfill/live-stream message behavior
- schema validation rules
- version compatibility rules

## Current Version

- Protocol: `ACP-0.1.0`
- Version file: `version/current.txt`

## Why ACP Exists

Odin merges price data and AI overlays into a single timeline. ACP provides a strict interface so independently built agents can interoperate with the backend router and chart UI without ambiguity.

## Key Protocol Decisions (ACP-0.1.0)

- Transport model:
  - Backend calls agent REST for historical backfill
  - Backend subscribes to agent WebSocket for live data
- Agent model: stateless per subscription
- Auth: none (local-only environment)
- Encoding: JSON
- Subscription scope: single symbol per subscription
- Intervals (canonical enum): `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`
- Delivery: at-least-once
- Dedup:
  - non-OHLC: `(agent_id, id)`
  - OHLC partial/final stream: `(agent_id, id, rev)`
- OHLC real-time policy:
  - emit partial updates for open bar (`bar_state=partial`)
  - emit terminal close (`bar_state=final`)
  - use monotonic `rev` per bar id

## Repository Layout

- `spec/ACP.md` — normative protocol spec
- `schemas/*.json` — normative JSON schemas
- `version/current.txt` — current ACP version string
- `examples/basic_test_agent/` — runnable local ACP test agent

## Quick Start (Local Test Agent)

```bash
cd examples/basic_test_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn agent:app --reload --host 127.0.0.1 --port 8010
```

Backfill example:

```bash
curl "http://127.0.0.1:8010/history?symbol=SPY&from=2026-03-01T10:00:00Z&to=2026-03-01T11:00:00Z&interval=1m"
```

## How To Make AI Coding Agents Follow ACP Precisely

Use this process for GitHub Copilot, Cursor, Cline, Aider, Claude Code, or other coding agents.

In Agent repo:
git submodule add git@github-personal:lfjewett/ACP.git protocols/acp
git -C protocols/acp checkout main

In Agent, add an AGENTS.md (and optionally .github/copilot-instructions.md) that says agents must read:
ACP.md
protocols/acp/schemas/*.json
protocols/acp/version/current.txt
