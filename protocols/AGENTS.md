# AGENTS.md

This file defines mandatory protocol constraints for any AI coding agent working in this repository.

## Authority

ACP is the source of truth for all protocol behavior.

Required read order before any code changes:
1. spec/ACP.md
2. schemas/messages.json
3. schemas/metadata.json
4. schemas/ohlc.json
5. schemas/session.json
6. schemas/line.json
7. schemas/event.json
8. schemas/band.json
9. schemas/histogram.json
10. schemas/forecast.json
11. version/current.txt

If there is any conflict between implementation code and ACP docs/schemas, ACP docs/schemas win.

## Mandatory Implementation Rules

- spec_version must equal the value in version/current.txt
- Subscription interval must be one of: 1m, 2m, 3m, 4m, 5m, 10m, 15m, 20m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 2d, 1w, 1M
- Subscriptions are single-symbol only
- No auth assumptions for ACP-0.3.0
- Transport model:
  - REST /metadata endpoint for base-URL discovery and capability negotiation
  - REST /history endpoint required only for `agent_type=price`
  - WebSocket for live data and bidirectional session traffic (not polled)
- Delivery semantics: at-least-once
- Dedup semantics:
  - non-OHLC: (agent_id, id)
  - OHLC: (agent_id, id, rev)
- OHLC stream semantics:
  - emit partial updates for open bar (bar_state=partial)
  - emit stream close as provisional_close (bar_state=provisional_close)
  - emit reconciliation updates as session_reconciled when backend confirms/polls REST state
  - emit final only when backend finalization policy marks a bar terminal
  - rev must be monotonic per bar id
  - session_id is required on ACP-0.3.0 WebSocket protocol messages
- Agent roles:
  - `price` | `indicator` | `event`
  - indicator selection is metadata-driven via `indicator_id` and params

## Required Output Contract For Agents

When an AI coding agent proposes or applies a protocol-related change, it must report:
1. Which ACP section(s) are affected
2. Which schema file(s) are validated
3. Whether protocol version changes are required
4. Any compatibility risk for existing agents/backends/UI

## Change Workflow

When protocol behavior changes, update in this order:
1. spec/ACP.md
2. affected schemas/*.json
3. version/current.txt (if version changed)
4. examples/ implementations that rely on changed behavior

Do not merge protocol-divergent implementation changes without corresponding ACP updates.

## Guardrails

- Do not invent new protocol fields unless explicitly requested and versioned.
- Do not expand interval enum unless explicitly approved.
- Do not switch OHLC semantics away from partial/provisional_close/session_reconciled/final + rev without a protocol change.
- If a task conflicts with ACP, stop and present:
  - ACP-compliant option
  - versioned protocol-change option
