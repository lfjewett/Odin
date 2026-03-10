from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="Basic ACP Overlay Agent", version="0.4.0")

SPEC_VERSION = "ACP-0.4.0"
AGENT_ID = "basic_ema_overlay"
MAX_RECORDS_PER_CHUNK = 5000


@dataclass
class OverlayState:
    alpha: float = 0.2
    last_ema: float | None = None
    candles_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)


session_states: dict[str, OverlayState] = {}
last_seq_received: dict[str, int] = {}
history_push_accumulators: dict[tuple[str, str], dict[str, Any]] = {}


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent_id": AGENT_ID, "spec_version": SPEC_VERSION}


@app.get("/metadata")
async def metadata() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "agent_id": AGENT_ID,
        "agent_name": "Basic EMA Overlay",
        "agent_version": "0.4.0",
        "description": "Example indicator agent for ACP 0.4.0 history_push/tick_update flow",
        "agent_type": "indicator",
        "data_dependency": "ohlc",
        "config_schema": {
            "alpha": {
                "type": "number",
                "description": "EMA smoothing factor",
                "required": False,
                "default": 0.2,
                "min": 0.01,
                "max": 1.0
            }
        },
        "outputs": [
            {
                "output_id": "ema.line",
                "schema": "line",
                "label": "EMA",
                "is_primary": True,
            }
        ],
        "indicators": [
            {
                "indicator_id": "ema",
                "name": "Exponential Moving Average",
                "description": "EMA line over close values",
                "params_schema": {
                    "alpha": {
                        "type": "number",
                        "description": "EMA smoothing factor",
                        "required": False,
                        "default": 0.2,
                        "min": 0.01,
                        "max": 1.0
                    }
                },
                "outputs": [
                    {
                        "output_id": "ema.line",
                        "schema": "line",
                        "label": "EMA",
                        "is_primary": True,
                    }
                ]
            }
        ],
        "transport_limits": {
            "max_records_per_chunk": MAX_RECORDS_PER_CHUNK,
            "max_websocket_message_bytes": 10485760,
            "chunk_timeout_seconds": 30,
        },
    }


def detect_gap_and_track(subscription_id: str, seq: int | None) -> bool:
    if seq is None:
        return False
    if subscription_id not in last_seq_received:
        last_seq_received[subscription_id] = seq
        return False
    expected = last_seq_received[subscription_id] + 1
    if seq != expected:
        return True
    last_seq_received[subscription_id] = seq
    return False


def apply_ema(state: OverlayState, close_value: float) -> float:
    if state.last_ema is None:
        state.last_ema = close_value
    else:
        state.last_ema = (close_value * state.alpha) + (state.last_ema * (1 - state.alpha))
    return state.last_ema


async def send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_json(payload)


async def send_chunked_history_response(
    websocket: WebSocket,
    session_id: str,
    subscription_id: str,
    overlays: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    total_records = len(overlays)
    total_chunks = max(1, (total_records + MAX_RECORDS_PER_CHUNK - 1) // MAX_RECORDS_PER_CHUNK)

    for chunk_index in range(total_chunks):
        start = chunk_index * MAX_RECORDS_PER_CHUNK
        end = min(start + MAX_RECORDS_PER_CHUNK, total_records)
        chunk_overlays = overlays[start:end]
        await send_json(
            websocket,
            {
                "type": "history_response",
                "spec_version": SPEC_VERSION,
                "session_id": session_id,
                "subscription_id": subscription_id,
                "agent_id": AGENT_ID,
                "schema": "line",
                "overlays": chunk_overlays,
                "metadata": metadata,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "is_final_chunk": chunk_index == total_chunks - 1,
            },
        )


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            session_id = msg.get("session_id")
            subscription_id = msg.get("subscription_id")

            if msg.get("spec_version") != SPEC_VERSION:
                await send_json(
                    websocket,
                    {
                        "type": "error",
                        "spec_version": SPEC_VERSION,
                        "session_id": session_id or "n/a",
                        "subscription_id": subscription_id or "n/a",
                        "agent_id": AGENT_ID,
                        "code": "INVALID_REQUEST",
                        "message": "Unsupported spec_version",
                        "retryable": False,
                    },
                )
                continue

            if not session_id or not subscription_id:
                await send_json(
                    websocket,
                    {
                        "type": "error",
                        "spec_version": SPEC_VERSION,
                        "session_id": session_id or "n/a",
                        "subscription_id": subscription_id or "n/a",
                        "agent_id": AGENT_ID,
                        "code": "INVALID_REQUEST",
                        "message": "Missing session_id or subscription_id",
                        "retryable": False,
                    },
                )
                continue

            state = session_states.setdefault(session_id, OverlayState())

            if msg_type == "subscribe":
                params = msg.get("params", {})
                alpha = params.get("alpha")
                if isinstance(alpha, (int, float)):
                    state.alpha = float(alpha)
                continue

            if msg_type == "unsubscribe":
                session_states.pop(session_id, None)
                last_seq_received.pop(subscription_id, None)
                history_push_accumulators.pop((session_id, subscription_id), None)
                continue

            if msg_type == "history_push":
                candles = msg.get("candles", [])
                if not isinstance(candles, list):
                    candles = []

                chunk_index = msg.get("chunk_index")
                total_chunks = msg.get("total_chunks")
                is_final_chunk = bool(msg.get("is_final_chunk"))

                all_candles = candles
                if chunk_index is not None:
                    key = (session_id, subscription_id)

                    try:
                        chunk_idx = int(chunk_index)
                        expected_total = int(total_chunks)
                    except (TypeError, ValueError):
                        await send_json(
                            websocket,
                            {
                                "type": "error",
                                "spec_version": SPEC_VERSION,
                                "session_id": session_id,
                                "subscription_id": subscription_id,
                                "agent_id": AGENT_ID,
                                "code": "CHUNK_SEQUENCE_ERROR",
                                "message": "Invalid chunk_index/total_chunks",
                                "retryable": False,
                            },
                        )
                        continue

                    if chunk_idx == 0:
                        history_push_accumulators[key] = {
                            "expected": 0,
                            "total": expected_total,
                            "candles": [],
                        }

                    accumulator = history_push_accumulators.get(key)
                    if not accumulator or int(accumulator["expected"]) != chunk_idx:
                        history_push_accumulators.pop(key, None)
                        await send_json(
                            websocket,
                            {
                                "type": "error",
                                "spec_version": SPEC_VERSION,
                                "session_id": session_id,
                                "subscription_id": subscription_id,
                                "agent_id": AGENT_ID,
                                "code": "CHUNK_SEQUENCE_ERROR",
                                "message": f"Expected chunk {accumulator['expected'] if accumulator else 0}, got {chunk_idx}",
                                "retryable": False,
                            },
                        )
                        continue

                    accumulator["candles"].extend(candles)
                    accumulator["expected"] = int(accumulator["expected"]) + 1

                    if not is_final_chunk:
                        continue

                    if int(accumulator["expected"]) != int(accumulator["total"]):
                        history_push_accumulators.pop(key, None)
                        await send_json(
                            websocket,
                            {
                                "type": "error",
                                "spec_version": SPEC_VERSION,
                                "session_id": session_id,
                                "subscription_id": subscription_id,
                                "agent_id": AGENT_ID,
                                "code": "CHUNK_SEQUENCE_ERROR",
                                "message": "Final chunk received before all chunks were delivered",
                                "retryable": False,
                            },
                        )
                        continue

                    all_candles = list(accumulator["candles"])
                    history_push_accumulators.pop(key, None)

                overlays: list[dict[str, Any]] = []
                state.last_ema = None
                state.candles_by_id = {}

                for candle in all_candles:
                    candle_id = candle.get("id")
                    close_value = candle.get("close")
                    ts = candle.get("ts")
                    if not candle_id or close_value is None or not ts:
                        continue
                    state.candles_by_id[candle_id] = candle
                    value = apply_ema(state, float(close_value))
                    overlays.append({"id": f"ema-{candle_id}", "ts": ts, "value": round(value, 6)})

                await send_chunked_history_response(
                    websocket,
                    session_id,
                    subscription_id,
                    overlays,
                    {
                        "alpha": state.alpha,
                        "computed_at": to_iso(datetime.now(UTC)),
                        "count": len(overlays),
                    },
                )
                continue

            if msg_type in {"tick_update", "candle_closed", "candle_correction"}:
                seq = msg.get("seq")
                if detect_gap_and_track(subscription_id, seq):
                    await send_json(
                        websocket,
                        {
                            "type": "resync_request",
                            "spec_version": SPEC_VERSION,
                            "session_id": session_id,
                            "subscription_id": subscription_id,
                            "agent_id": AGENT_ID,
                            "last_seq_received": last_seq_received.get(subscription_id, 0),
                        },
                    )
                    continue

                candle = msg.get("candle", {})
                candle_id = candle.get("id")
                ts = candle.get("ts")
                close_value = candle.get("close")
                if not candle_id or ts is None or close_value is None:
                    continue

                state.candles_by_id[candle_id] = candle
                value = apply_ema(state, float(close_value))
                await send_json(
                    websocket,
                    {
                        "type": "overlay_update",
                        "spec_version": SPEC_VERSION,
                        "session_id": session_id,
                        "subscription_id": subscription_id,
                        "agent_id": AGENT_ID,
                        "schema": "line",
                        "record": {
                            "id": f"ema-{candle_id}",
                            "ts": ts,
                            "value": round(value, 6),
                        },
                    },
                )
                continue

            if msg_type == "resync_response":
                replay_messages = msg.get("messages", [])
                for replay in replay_messages:
                    replay_seq = replay.get("seq")
                    if replay_seq is not None:
                        last_seq_received[subscription_id] = replay_seq
                continue

            await send_json(
                websocket,
                {
                    "type": "error",
                    "spec_version": SPEC_VERSION,
                    "session_id": session_id,
                    "subscription_id": subscription_id,
                    "agent_id": AGENT_ID,
                    "code": "UNSUPPORTED_OPERATION",
                    "message": f"Unsupported message type '{msg_type}'",
                    "retryable": False,
                },
            )

    except WebSocketDisconnect:
        pass
