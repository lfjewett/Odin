# instructions.md

Use this as a copy/paste bootstrap prompt for any coding assistant session (Copilot, Cursor, Cline, Aider, Claude Code, etc.).

## Session Bootstrap Prompt

Treat ACP as the authoritative protocol contract for this repository.

Before making any changes, read:
- spec/ACP.md
- schemas/messages.json
- schemas/metadata.json
- schemas/ohlc.json
- schemas/line.json
- schemas/event.json
- schemas/band.json
- schemas/histogram.json
- schemas/forecast.json
- version/current.txt

Then enforce these rules exactly:
- spec_version equals version/current.txt
- interval is one of: 1m, 5m, 15m, 30m, 1h, 4h, 1d
- subscriptions are single-symbol only
- transport is REST backfill + WebSocket live stream
- delivery is at-least-once
- dedup is:
  - non-OHLC (agent_id,id)
  - OHLC (agent_id,id,rev)
- OHLC supports real-time partial/final updates with:
  - bar_state in {partial, final}
  - monotonic rev per bar id
- no auth and no discovery assumptions for ACP-0.1.0
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
