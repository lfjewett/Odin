# UI/Backend Sync Contract

This document defines the runtime synchronization contract between backend state and frontend UI reconciliation.

## Purpose

Ensure UI state remains correct without manual refresh by using domain revisions, invalidation events, and authoritative sync snapshots.

## Domains

The backend tracks independent monotonic revisions for:

- `agent`
- `overlay`
- `trade`
- `workspace`

A higher revision always supersedes a lower revision for the same domain.

## Message Types

### 1) `state_event`

Backend -> Frontend notification that a domain changed.

Required fields:

- `type`: `"state_event"`
- `domain`: one of `agent | overlay | trade | workspace`
- `reason`: machine-readable reason string
- `revision`: revision for the changed domain after increment
- `server_revisions`: full revision map snapshot
- `server_ts`: server timestamp in milliseconds

Optional fields:

- `session_id`: when event is session-scoped
- `payload`: partial domain payload (may be omitted)

Semantics:

- Frontend marks domain stale when `server_revisions[domain]` exceeds local known revision.
- Frontend may reconcile immediately from payload when included.
- Frontend should request/await sync snapshot for authoritative merge.

### 2) `client_sync`

Frontend -> Backend request for authoritative state when stale domains are detected or on periodic reconciliation.

Required fields:

- `type`: `"client_sync"`
- `client_revisions`: client-known revision map

Optional fields:

- `session_id`: currently focused session

Semantics:

- Backend compares revisions and returns a `sync_snapshot`.

### 3) `sync_snapshot`

Backend -> Frontend authoritative state response to `client_sync`.

Required fields:

- `type`: `"sync_snapshot"`
- `server_revisions`: full server revision map
- `server_ts`: server timestamp in milliseconds

Optional domain payloads (included when needed):

- `agent_list`
- `overlay_sessions`
- `trade_sessions`
- `workspace`

Semantics:

- Frontend must treat this as source of truth.
- Frontend applies included payloads and advances local revisions to `server_revisions`.
- If a stale domain has no payload, frontend keeps stale/syncing handling and may trigger domain-specific recovery (e.g., trade strategy reapply for active session).

## Conflict Rules

1. Revisions are monotonic per domain and server-authoritative.
2. Payload data is accepted only when it matches/supersedes local domain revision.
3. UI must never clear stable domain data on invalidation without either:
   - a replacement payload, or
   - an active recovery path.

## Recovery Rules

- Subscription failures (`SUBSCRIPTION_NOT_FOUND`) should trigger backend recovery/forced resubscribe.
- Invalid indicator params are sanitized/clamped before subscribe.
- Trade stale state without payload can trigger throttled frontend auto-heal reapply.

## Implementation References

Backend:

- `backend/app/main.py`
  - domain revisions and `emit_state_event`
  - `handle_client_sync_request` (`sync_snapshot`)
  - websocket route handling `client_sync`
- `backend/app/agent_connection.py`
  - forced resubscribe support (`subscribe(..., force=True)`)

Frontend:

- `frontend/src/stream/useEventStream.ts`
  - receives `state_event` and `sync_snapshot`
  - sends periodic `client_sync`
- `frontend/src/hooks/useSyncCoordinator.ts`
  - local revision/stale-domain tracking
- `frontend/src/App.tsx`
  - domain reconciliation and trade auto-heal behavior

## Change Policy

Any UI/backend change that affects state lifecycle, revisions, subscriptions, snapshots, or reconciliation logic must update this contract document in the same PR.
