# instructions.md

Use this as a copy/paste bootstrap prompt for any coding assistant session (Copilot, Cursor, Cline, Aider, Claude Code, etc.).

## Session Bootstrap Prompt

Treat ACP as the authoritative protocol contract for this repository.

Before making any changes, read:
- spec/ACP.md
- schemas/messages.json
- schemas/metadata.json
- schemas/ohlc.json
- schemas/session.json
- schemas/line.json
- schemas/event.json
- schemas/band.json
- schemas/histogram.json
- schemas/forecast.json
- version/current.txt

Then enforce these rules exactly:
- spec_version equals version/current.txt
- interval is one of: 1m, 2m, 3m, 4m, 5m, 10m, 15m, 20m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 2d, 1w, 1M
- subscriptions are single-symbol only
- transport is REST metadata + WebSocket live/bidirectional stream
- `/history` is required only for `agent_type=price`
- indicator discovery is metadata-driven from base URL (`indicators[]`)
- delivery is at-least-once
- dedup is:
  - non-OHLC (agent_id,id)
  - OHLC (agent_id,id,rev)
- OHLC supports lifecycle updates with:
  - bar_state in {partial, provisional_close, session_reconciled, final}
  - monotonic rev per bar id
- session_id required on ACP-0.4.0 WebSocket protocol messages
- no auth assumptions for ACP-0.4.0
- do not introduce protocol fields outside ACP unless proposed as a versioned change

For every protocol-related task response, include:
1) ACP sections applied
2) schema files validated
3) whether a version bump is needed
4) any compatibility implications

If a requested change conflicts with ACP, stop and provide:
- Option A: ACP-compliant implementation
- Option B: explicit ACP version-change plan

## Optional One-Line Prompt (short form)

Read AGENTS.md and enforce ACP strictly before coding; reject non-compliant protocol changes unless accompanied by a versioned spec/schema update.
