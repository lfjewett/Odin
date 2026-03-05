# AGENTS.md

This file defines mandatory protocol constraints for any AI coding agent working in this repository.

## Authority

ACP is the source of truth for all protocol behavior.

Required read order before any code changes:
1. spec/ACP.md
2. schemas/messages.json
3. schemas/metadata.json
4. schemas/ohlc.json
5. schemas/line.json
6. schemas/event.json
7. schemas/band.json
8. schemas/histogram.json
9. schemas/forecast.json
10. version/current.txt

If there is any conflict between implementation code and ACP docs/schemas, ACP docs/schemas win.

## Mandatory Implementation Rules

- spec_version must equal the value in version/current.txt
- Subscription interval must be one of: 1m, 5m, 15m, 30m, 1h, 4h, 1d
- Subscriptions are single-symbol only
- No auth/discovery assumptions for ACP-0.1.0
- Transport model:
  - REST /metadata endpoint for agent configuration discovery
  - REST /history endpoint for backfill
  - WebSocket for live data (not polled)
- Delivery semantics: at-least-once
- Dedup semantics:
  - non-OHLC: (agent_id, id)
  - OHLC: (agent_id, id, rev)
- OHLC stream semantics:
  - emit partial updates for open bar (bar_state=partial)
  - emit terminal final update (bar_state=final)
  - rev must be monotonic per bar id

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
- Do not switch OHLC semantics away from partial/final + rev without a protocol change.
- If a task conflicts with ACP, stop and present:
  - ACP-compliant option
  - versioned protocol-change option
