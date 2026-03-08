from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

app = FastAPI(title="Basic ACP Price Agent", version="0.2.0")

SPEC_VERSION = "ACP-0.2.0"
AGENT_ID = "basic_price_agent"
ALLOWED_SYMBOLS = {"SPY"}
INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass
class SubscriptionState:
    session_id: str
    subscription_id: str
    symbol: str
    interval: str
    started_monotonic: float
    last_event_ts: str
    running: bool


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def interval_floor(dt: datetime, seconds: int) -> datetime:
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=UTC)


def make_seq(interval: str, bar_start: datetime) -> int:
    interval_s = INTERVAL_SECONDS[interval]
    return int(bar_start.timestamp()) // interval_s


def make_bar_id(symbol: str, interval: str, bar_start: datetime) -> str:
    return f"{symbol}:{interval}:{int(bar_start.timestamp())}"


def seeded_price(symbol: str, interval: str, bar_start: datetime) -> float:
    src = f"{symbol}|{interval}|{int(bar_start.timestamp())}"
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) % 2000
    return 350.0 + (value / 10.0)


def generate_history_bar(symbol: str, interval: str, bar_start: datetime, now: datetime) -> dict[str, Any]:
    base = seeded_price(symbol, interval, bar_start)
    rng = random.Random(f"{symbol}|{interval}|{int(bar_start.timestamp())}")
    drift = rng.uniform(-0.8, 0.8)
    open_price = round(base, 2)
    close_price = round(base + drift, 2)
    high_price = round(max(open_price, close_price) + abs(rng.uniform(0.0, 0.5)), 2)
    low_price = round(min(open_price, close_price) - abs(rng.uniform(0.0, 0.5)), 2)
    volume = rng.randint(800, 8000)

    age = now - bar_start
    if age >= timedelta(days=1):
        bar_state = "final"
        rev = 2
    elif age >= timedelta(minutes=10):
        bar_state = "session_reconciled"
        rev = 1
    else:
        bar_state = "provisional_close"
        rev = 0

    return {
        "id": make_bar_id(symbol, interval, bar_start),
        "seq": make_seq(interval, bar_start),
        "rev": rev,
        "bar_state": bar_state,
        "ts": to_iso(bar_start),
        "open": open_price,
        "high": max(open_price, high_price, close_price),
        "low": min(open_price, low_price, close_price),
        "close": close_price,
        "volume": volume,
    }


def error_payload(
    session_id: str | None,
    subscription_id: str | None,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "error",
        "spec_version": SPEC_VERSION,
        "session_id": session_id or "n/a",
        "subscription_id": subscription_id or "n/a",
        "agent_id": AGENT_ID,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if details:
        payload["details"] = details
    return payload


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent_id": AGENT_ID, "spec_version": SPEC_VERSION}


@app.get("/metadata")
async def metadata() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "agent_id": AGENT_ID,
        "agent_name": "Basic Price Agent",
        "agent_version": "0.2.0",
        "description": "Generates synthetic OHLC price data for testing ACP 0.2.0 lifecycle behavior",
        "agent_type": "price",
        "data_dependency": "none",
        "config_schema": {},
        "output_schema": "ohlc",
        "overlay": {
            "kind": "ohlc",
            "panel": "price",
            "color": "#3b82f6",
            "legend": f"{AGENT_ID} candles",
        },
    }


@app.get("/history")
async def history(
    symbol: str,
    from_ts: str = Query(alias="from"),
    to_ts: str = Query(alias="to"),
    interval: str = Query(...),
) -> dict[str, Any]:
    if symbol not in ALLOWED_SYMBOLS:
        return error_payload(None, None, "INVALID_SYMBOL", f"Unsupported symbol '{symbol}'", False, {"symbol": symbol})
    if interval not in INTERVAL_SECONDS:
        return error_payload(None, None, "INVALID_INTERVAL", f"Unsupported interval '{interval}'", False, {"interval": interval})

    try:
        start = parse_iso(from_ts)
        end = parse_iso(to_ts)
    except ValueError:
        return error_payload(None, None, "INVALID_REQUEST", "Invalid ISO-8601 timestamp in from/to", False)

    if end <= start:
        return error_payload(None, None, "INVALID_REQUEST", "'to' must be greater than 'from'", False)

    step = INTERVAL_SECONDS[interval]
    cursor = interval_floor(start, step)
    bars: list[dict[str, Any]] = []
    now = utc_now()

    while cursor < end:
        if cursor >= start:
            bars.append(generate_history_bar(symbol, interval, cursor, now))
        cursor += timedelta(seconds=step)

    return {
        "spec_version": SPEC_VERSION,
        "agent_id": AGENT_ID,
        "schema": "ohlc",
        "symbol": symbol,
        "interval": interval,
        "data": bars,
    }


async def send_with_lock(websocket: WebSocket, lock: asyncio.Lock, payload: dict[str, Any]) -> None:
    async with lock:
        await websocket.send_json(payload)


async def emit_session_reconciliation(
    websocket: WebSocket,
    lock: asyncio.Lock,
    state: SubscriptionState,
    candle: dict[str, Any],
) -> None:
    await asyncio.sleep(2)
    corrected = dict(candle)
    corrected["rev"] = candle["rev"] + 1
    corrected["bar_state"] = "session_reconciled"

    await send_with_lock(
        websocket,
        lock,
        {
            "type": "data",
            "spec_version": SPEC_VERSION,
            "session_id": state.session_id,
            "subscription_id": state.subscription_id,
            "agent_id": AGENT_ID,
            "schema": "ohlc",
            "record": corrected,
        },
    )
    state.last_event_ts = corrected["ts"]


async def stream_subscription(websocket: WebSocket, lock: asyncio.Lock, state: SubscriptionState) -> None:
    interval_s = INTERVAL_SECONDS[state.interval]
    bar_start = interval_floor(utc_now(), interval_s)
    bar_end = bar_start + timedelta(seconds=interval_s)

    open_price = seeded_price(state.symbol, state.interval, bar_start)
    high_price = open_price
    low_price = open_price
    close_price = open_price
    volume = 0.0
    rev = 0
    seq = make_seq(state.interval, bar_start)
    rng = random.Random(f"{state.subscription_id}|{state.symbol}|{state.interval}")

    while state.running:
        now = utc_now()
        if now >= bar_end:
            close_record = {
                "id": make_bar_id(state.symbol, state.interval, bar_start),
                "seq": seq,
                "rev": rev,
                "bar_state": "provisional_close",
                "ts": to_iso(bar_start),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": round(volume, 0),
            }
            await send_with_lock(
                websocket,
                lock,
                {
                    "type": "data",
                    "spec_version": SPEC_VERSION,
                    "session_id": state.session_id,
                    "subscription_id": state.subscription_id,
                    "agent_id": AGENT_ID,
                    "schema": "ohlc",
                    "record": close_record,
                },
            )
            state.last_event_ts = close_record["ts"]
            asyncio.create_task(emit_session_reconciliation(websocket, lock, state, close_record))

            bar_start = bar_end
            bar_end = bar_start + timedelta(seconds=interval_s)
            open_price = close_price
            high_price = open_price
            low_price = open_price
            volume = 0.0
            rev = 0
            seq = make_seq(state.interval, bar_start)

        delta = rng.uniform(-0.12, 0.12)
        close_price = max(0.01, close_price + delta)
        high_price = max(high_price, close_price)
        low_price = min(low_price, close_price)
        volume += max(1.0, rng.uniform(4.0, 25.0))

        partial_record = {
            "id": make_bar_id(state.symbol, state.interval, bar_start),
            "seq": seq,
            "rev": rev,
            "bar_state": "partial",
            "ts": to_iso(bar_start),
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "volume": round(volume, 0),
        }
        await send_with_lock(
            websocket,
            lock,
            {
                "type": "data",
                "spec_version": SPEC_VERSION,
                "session_id": state.session_id,
                "subscription_id": state.subscription_id,
                "agent_id": AGENT_ID,
                "schema": "ohlc",
                "record": partial_record,
            },
        )
        state.last_event_ts = partial_record["ts"]
        rev += 1
        await asyncio.sleep(1)


async def heartbeat_subscription(websocket: WebSocket, lock: asyncio.Lock, state: SubscriptionState) -> None:
    while state.running:
        uptime_s = int(time.monotonic() - state.started_monotonic)
        await send_with_lock(
            websocket,
            lock,
            {
                "type": "heartbeat",
                "spec_version": SPEC_VERSION,
                "session_id": state.session_id,
                "subscription_id": state.subscription_id,
                "agent_id": AGENT_ID,
                "status": "ok",
                "uptime_s": uptime_s,
                "last_event_ts": state.last_event_ts,
            },
        )
        await asyncio.sleep(10)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    states: dict[str, SubscriptionState] = {}
    tasks: dict[str, tuple[asyncio.Task[Any], asyncio.Task[Any]]] = {}

    async def stop_subscription(subscription_id: str) -> None:
        if subscription_id in states:
            states[subscription_id].running = False
        if subscription_id in tasks:
            stream_task, heartbeat_task = tasks.pop(subscription_id)
            stream_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(stream_task, heartbeat_task, return_exceptions=True)
            states.pop(subscription_id, None)

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            session_id = msg.get("session_id")
            subscription_id = msg.get("subscription_id")

            if msg_type not in {"subscribe", "unsubscribe", "reconfigure"}:
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, subscription_id, "INVALID_REQUEST", f"Unsupported message type '{msg_type}'", False),
                )
                continue

            if msg.get("spec_version") != SPEC_VERSION:
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, subscription_id, "INVALID_REQUEST", "Unsupported spec_version", False),
                )
                continue

            if not session_id or not isinstance(session_id, str):
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(None, subscription_id, "INVALID_REQUEST", "Missing session_id", False),
                )
                continue

            if not subscription_id or not isinstance(subscription_id, str):
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, None, "INVALID_REQUEST", "Missing subscription_id", False),
                )
                continue

            if msg_type == "unsubscribe":
                if subscription_id not in states:
                    await send_with_lock(
                        websocket,
                        send_lock,
                        error_payload(session_id, subscription_id, "SUBSCRIPTION_NOT_FOUND", "Subscription not found", False),
                    )
                    continue
                await stop_subscription(subscription_id)
                continue

            symbol = msg.get("symbol")
            interval = msg.get("interval")
            if symbol not in ALLOWED_SYMBOLS:
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, subscription_id, "INVALID_SYMBOL", f"Unsupported symbol '{symbol}'", False, {"symbol": symbol}),
                )
                continue
            if interval not in INTERVAL_SECONDS:
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, subscription_id, "INVALID_INTERVAL", f"Unsupported interval '{interval}'", False, {"interval": interval}),
                )
                continue

            if msg_type == "reconfigure" and "new_params" not in msg:
                await send_with_lock(
                    websocket,
                    send_lock,
                    error_payload(session_id, subscription_id, "INVALID_PARAMS", "Missing new_params for reconfigure", False),
                )
                continue

            if subscription_id in states:
                await stop_subscription(subscription_id)

            state = SubscriptionState(
                session_id=session_id,
                subscription_id=subscription_id,
                symbol=symbol,
                interval=interval,
                started_monotonic=time.monotonic(),
                last_event_ts=to_iso(utc_now()),
                running=True,
            )
            states[subscription_id] = state
            stream_task = asyncio.create_task(stream_subscription(websocket, send_lock, state))
            heartbeat_task = asyncio.create_task(heartbeat_subscription(websocket, send_lock, state))
            tasks[subscription_id] = (stream_task, heartbeat_task)

    except WebSocketDisconnect:
        pass
    finally:
        for subscription_id in list(tasks.keys()):
            await stop_subscription(subscription_id)
