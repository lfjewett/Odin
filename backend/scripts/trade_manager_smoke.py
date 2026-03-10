from __future__ import annotations

import asyncio
import json
import sys
import uuid

import httpx
import websockets

BACKEND_HTTP = "http://127.0.0.1:8001"
BACKEND_WS = "ws://127.0.0.1:8001/ws"


async def require_price_agent(client: httpx.AsyncClient) -> str:
    response = await client.get(f"{BACKEND_HTTP}/api/agents")
    response.raise_for_status()
    payload = response.json()
    agents = payload.get("agents", [])
    for agent in agents:
        if agent.get("agent_type") == "price":
            return str(agent.get("id"))
    raise RuntimeError("No price agent found; cannot create session for smoke test")


async def wait_for_connection_ready(ws) -> None:
    raw = await ws.recv()
    msg = json.loads(raw)
    if msg.get("type") != "connection_ready":
        raise RuntimeError(f"Expected connection_ready, got: {msg}")


async def subscribe_session(ws, agent_id: str, session_id: str) -> None:
    subscribe_payload = {
        "type": "subscribe_request",
        "session_id": session_id,
        "agent_id": agent_id,
        "symbol": "SPY",
        "interval": "1m",
        "timeframe_days": 1,
    }
    await ws.send(json.dumps(subscribe_payload))

    snapshot_received = False
    for _ in range(20):
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        message = json.loads(raw)
        if message.get("type") == "snapshot" and message.get("session_id") == session_id:
            snapshot_received = True
            break

    if not snapshot_received:
        raise RuntimeError("Did not receive snapshot after subscribe_request")


async def run() -> int:
    session_id = f"smoke-{uuid.uuid4().hex[:8]}"
    strategy_name = "Smoke Strategy"

    async with httpx.AsyncClient(timeout=20.0) as client:
        health = await client.get(f"{BACKEND_HTTP}/health")
        health.raise_for_status()

        agent_id = await require_price_agent(client)

        async with websockets.connect(BACKEND_WS) as ws:
            await wait_for_connection_ready(ws)
            await subscribe_session(ws=ws, agent_id=agent_id, session_id=session_id)

            validate_payload = {
                "entry_rule": "CLOSE < OPEN AND !IN_BULL_TRADE",
                "exit_rule": "CLOSE > OPEN AND IN_BULL_TRADE",
            }
            validate = await client.post(
                f"{BACKEND_HTTP}/api/sessions/{session_id}/trade-strategies/validate",
                json=validate_payload,
            )
            validate.raise_for_status()
            validate_body = validate.json()
            if not validate_body.get("valid"):
                raise RuntimeError(f"Validation failed unexpectedly: {validate_body}")

            save_payload = {
                "description": "Smoke test strategy",
                "entry_rule": validate_payload["entry_rule"],
                "exit_rule": validate_payload["exit_rule"],
            }
            save = await client.put(
                f"{BACKEND_HTTP}/api/sessions/{session_id}/trade-strategies/{strategy_name}",
                json=save_payload,
            )
            save.raise_for_status()

            listed = await client.get(f"{BACKEND_HTTP}/api/sessions/{session_id}/trade-strategies")
            listed.raise_for_status()
            list_body = listed.json()
            names = [item.get("name") for item in list_body.get("strategies", [])]
            if strategy_name not in names:
                raise RuntimeError(f"Saved strategy missing from list: {list_body}")

            apply = await client.post(
                f"{BACKEND_HTTP}/api/sessions/{session_id}/trade-strategies/apply",
                json={"strategy_name": strategy_name},
            )
            apply.raise_for_status()
            apply_body = apply.json()
            if "markers" not in apply_body or "marker_count" not in apply_body:
                raise RuntimeError(f"Apply response missing marker payload: {apply_body}")

            delete = await client.delete(
                f"{BACKEND_HTTP}/api/sessions/{session_id}/trade-strategies/{strategy_name}",
            )
            delete.raise_for_status()

            unsubscribe_payload = {
                "type": "unsubscribe_request",
                "session_id": session_id,
            }
            await ws.send(json.dumps(unsubscribe_payload))

        print("Smoke test passed")
        print(f"Session: {session_id}")
        print(f"Marker count: {apply_body.get('marker_count')}")

    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(run())
    except Exception as exc:
        print(f"Smoke test failed: {exc}")
        sys.exit(1)
    sys.exit(code)
