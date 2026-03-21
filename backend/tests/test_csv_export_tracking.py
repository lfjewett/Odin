from __future__ import annotations

import asyncio

import app.main as main
from app.agent_data_store import SessionDataStore


def make_store(session_id: str) -> SessionDataStore:
    store = SessionDataStore(
        session_id=session_id,
        agent_id="price-agent",
        symbol="SPY",
        interval="1m",
    )
    store.ingest_ohlc(
        {
            "id": "SPY:1m:1",
            "ts": "2026-03-11T13:30:00Z",
            "open": 500.0,
            "high": 501.0,
            "low": 499.5,
            "close": 500.5,
            "volume": 1000,
            "bar_state": "final",
            "rev": 0,
        }
    )
    return store


def teardown_function() -> None:
    main.export_history_response_state.clear()


def test_export_settle_times_out_when_indicator_history_missing() -> None:
    session_id = "export-missing-sr"
    store = make_store(session_id)
    main._begin_export_history_tracking(session_id, {"odin_indicator_agent__sr", "odin_indicator_agent__vwap"})
    main._mark_export_history_response(
        session_id,
        "odin_indicator_agent__vwap",
        f"{session_id}::odin_indicator_agent__vwap::v1",
        3,
    )

    async def run() -> None:
        try:
            await main._wait_for_export_settle(
                store,
                min_delay_seconds=0,
                poll_seconds=0.01,
                timeout_seconds=0.05,
                session_id=session_id,
            )
        except TimeoutError as exc:
            assert "odin_indicator_agent__sr" in str(exc)
            return
        raise AssertionError("Expected settle wait to time out while SR history_response is missing")

    asyncio.run(run())


def test_export_settle_waits_for_slow_indicator_history_response() -> None:
    session_id = "export-slow-sr"
    store = make_store(session_id)
    main._begin_export_history_tracking(session_id, {"odin_indicator_agent__sr"})

    async def emit_late_history_response() -> None:
        await asyncio.sleep(0.02)
        main._mark_export_history_response(
            session_id,
            "odin_indicator_agent__sr",
            f"{session_id}::odin_indicator_agent__sr::v1",
            12,
        )

    async def run() -> None:
        marker_task = asyncio.create_task(emit_late_history_response())
        await main._wait_for_export_settle(
            store,
            min_delay_seconds=0,
            poll_seconds=0.01,
            timeout_seconds=0.2,
            session_id=session_id,
        )
        await marker_task
        assert main._pending_export_history_agents(session_id) == set()

    asyncio.run(run())
