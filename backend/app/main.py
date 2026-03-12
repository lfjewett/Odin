"""
Odin Backend - FastAPI WebSocket Server (ACP v0.4.2)

Manages WebSocket connections from the frontend and routes them to ACP agents:
- Accepts WebSocket connections from the frontend (each is a client_id)
- Loads agent configurations from overlay_agents.yaml (ACP v0.4.2)
- Connects to ACP agents via WebSocket
- Routes ACP messages to specific sessions (not broadcast-all)
- Maintains canonical candle store per session with deduplication
- Provides REST API for agent management
"""

from __future__ import annotations

import asyncio
import copy
import csv
import logging
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any, Set
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent_data_store import SessionDataStore
from app.agent_manager import agent_manager
from app.models import (
    AgentConfig,
    SessionManager,
    UI_MANAGED_INDICATOR_CONFIG_KEYS,
    Variable,
    build_area_field_variable_name,
    build_area_metadata_variable_name,
    build_variable_name,
    infer_selected_indicator_id,
    normalize_indicator_config,
)
from app.trade_engine import evaluate_research_expression, evaluate_strategy, validate_strategy_v2
from app.trade_strategy_store import TradeStrategyStore
from app.workspace_store import WorkspaceStore

ACP_SPEC_VERSION = "ACP-0.4.2"
ACP_API_VERSION = "0.4.2"
COMPATIBLE_ACP_SPEC_VERSIONS = {"ACP-0.4.0", "ACP-0.4.1", "ACP-0.4.2"}
DEFAULT_CHUNK_TIMEOUT_SECONDS = 30
SUBSCRIBE_PROPAGATION_DELAY_SECONDS = 0.2
EXPORT_CHUNK_MONTHS = 6
EXPORT_SETTLE_MIN_DELAY_SECONDS = 6.0
EXPORT_SETTLE_POLL_SECONDS = 2.0
EXPORT_SETTLE_TIMEOUT_SECONDS = 120.0
TRADE_RECOMPUTE_WARN_MS = 750.0
OVERLAY_INGEST_WARN_RPS = 1500.0
INDICATOR_OHLC_FIELDS = {
    "id",
    "seq",
    "rev",
    "bar_state",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def to_indicator_ohlc(candle: dict) -> dict:
    """Normalize candles to strict ACP OHLC fields expected by indicator agents."""
    return {key: candle[key] for key in INDICATOR_OHLC_FIELDS if key in candle}


def next_available_indicator_instance(base_agent_id: str) -> tuple[str, int]:
    """Return a unique runtime indicator agent_id and instance index."""
    if not agent_manager.get_agent(base_agent_id):
        return base_agent_id, 1

    index = 2
    while True:
        candidate = f"{base_agent_id}__{index}"
        if not agent_manager.get_agent(candidate):
            return candidate, index
        index += 1


class DiscoverAgentRequest(BaseModel):
    agent_url: str = Field(min_length=1)


class AddAgentRequest(BaseModel):
    agent_url: str = Field(min_length=1)
    indicator_id: str | None = None
    params: dict = Field(default_factory=dict)


class UpdateAgentRequest(BaseModel):
    agent_name: str | None = None
    params: dict = Field(default_factory=dict)


class UpsertWorkspaceRequest(BaseModel):
    schema_version: int = 1
    state: dict[str, Any] = Field(default_factory=dict)


class UpsertTradeStrategyRequest(BaseModel):
    description: str = ""
    long_entry_rules: list[str] = Field(default_factory=list)
    long_exit_rules: list[str] = Field(default_factory=list)
    short_entry_rules: list[str] = Field(default_factory=list)
    short_exit_rules: list[str] = Field(default_factory=list)
    # Backward compatibility: accept single-string format
    long_entry_rule: str = ""
    short_entry_rule: str = ""


class ValidateTradeStrategyRequest(BaseModel):
    long_entry_rules: list[str] = Field(default_factory=list)
    long_exit_rules: list[str] = Field(default_factory=list)
    short_entry_rules: list[str] = Field(default_factory=list)
    short_exit_rules: list[str] = Field(default_factory=list)
    # Backward compatibility: accept single-string format
    long_entry_rule: str = ""
    short_entry_rule: str = ""


class ApplyTradeStrategyRequest(BaseModel):
    strategy_name: str | None = None
    long_entry_rules: list[str] = Field(default_factory=list)
    long_exit_rules: list[str] = Field(default_factory=list)
    short_entry_rules: list[str] = Field(default_factory=list)
    short_exit_rules: list[str] = Field(default_factory=list)
    # Backward compatibility: accept single-string format
    long_entry_rule: str = ""
    short_entry_rule: str = ""


class EvaluateResearchExpressionRequest(BaseModel):
    expression: str = Field(min_length=1)
    output_schema: str = Field(default="line")


class CreateCsvExportRequest(BaseModel):
    start_date: str = Field(min_length=1)
    end_date: str = Field(min_length=1)
    interval: str = Field(default="1m")
    settle_min_delay_seconds: float = Field(default=EXPORT_SETTLE_MIN_DELAY_SECONDS, ge=0)
    settle_poll_seconds: float = Field(default=EXPORT_SETTLE_POLL_SECONDS, gt=0)
    settle_timeout_seconds: float = Field(default=EXPORT_SETTLE_TIMEOUT_SECONDS, gt=0)


def _normalize_strategy_rules_payload(payload: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    # Support both new array format and old single-string format
    long_entry_rules_list = payload.get("long_entry_rules", [])
    long_entry_rule_str = payload.get("long_entry_rule", "")
    if isinstance(long_entry_rules_list, list):
        long_entry_rules = [str(rule).strip() for rule in long_entry_rules_list if str(rule).strip()]
    else:
        long_entry_rules = []
    if long_entry_rule_str and not long_entry_rules:
        long_entry_rules = [str(long_entry_rule_str).strip()]

    raw_long_exits = payload.get("long_exit_rules")
    if isinstance(raw_long_exits, list):
        long_exit_rules = [str(rule).strip() for rule in raw_long_exits if str(rule).strip()]
    else:
        long_exit_rules = []

    short_entry_rules_list = payload.get("short_entry_rules", [])
    short_entry_rule_str = payload.get("short_entry_rule", "")
    if isinstance(short_entry_rules_list, list):
        short_entry_rules = [str(rule).strip() for rule in short_entry_rules_list if str(rule).strip()]
    else:
        short_entry_rules = []
    if short_entry_rule_str and not short_entry_rules:
        short_entry_rules = [str(short_entry_rule_str).strip()]

    raw_short_exits = payload.get("short_exit_rules")
    if isinstance(raw_short_exits, list):
        short_exit_rules = [str(rule).strip() for rule in raw_short_exits if str(rule).strip()]
    else:
        short_exit_rules = []

    return long_entry_rules, long_exit_rules, short_entry_rules, short_exit_rules


def sanitize_indicator_params(
    params: dict[str, Any] | None,
    indicators_catalog: list[dict[str, Any]] | None,
    selected_indicator_id: str | None = None,
) -> dict[str, Any]:
    """
    Normalize indicator params to agent-declared schema ranges/types.

    Prevents subscribe rejection (e.g., period out of allowed bounds).
    Unknown params are dropped.
    """
    incoming = dict(params or {})
    catalog = indicators_catalog or []

    schema_by_name: dict[str, dict[str, Any]] = {}
    for indicator in catalog:
        if selected_indicator_id and indicator.get("indicator_id") != selected_indicator_id:
            continue

        params_schema = indicator.get("params_schema")
        if isinstance(params_schema, dict):
            for key, schema in params_schema.items():
                if isinstance(schema, dict):
                    schema_by_name[str(key)] = schema

        if selected_indicator_id:
            break

    normalized: dict[str, Any] = {}

    aggregation_interval = incoming.get("aggregation_interval")
    if isinstance(aggregation_interval, str) and aggregation_interval.strip():
        normalized["aggregation_interval"] = aggregation_interval.strip()

    if not schema_by_name:
        return normalized

    for key, schema in schema_by_name.items():
        if key not in incoming:
            continue

        value = incoming.get(key)
        field_type = str(schema.get("type") or "").lower()
        min_value = schema.get("min")
        max_value = schema.get("max")

        try:
            if field_type == "integer":
                if isinstance(value, bool):
                    raise ValueError("bool is not valid integer param")
                coerced = int(value)
                if isinstance(min_value, (int, float)):
                    coerced = max(coerced, int(min_value))
                if isinstance(max_value, (int, float)):
                    coerced = min(coerced, int(max_value))
                normalized[key] = coerced
            elif field_type in {"number", "float"}:
                coerced_float = float(value)
                if isinstance(min_value, (int, float)):
                    coerced_float = max(coerced_float, float(min_value))
                if isinstance(max_value, (int, float)):
                    coerced_float = min(coerced_float, float(max_value))
                normalized[key] = coerced_float
            elif field_type == "boolean":
                if isinstance(value, str):
                    normalized[key] = value.strip().lower() == "true"
                else:
                    normalized[key] = bool(value)
            else:
                normalized[key] = value
        except (TypeError, ValueError):
            if "default" in schema:
                normalized[key] = schema.get("default")
            elif isinstance(min_value, (int, float)):
                normalized[key] = int(min_value) if field_type == "integer" else float(min_value)

    return normalized

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("ODIN_LOG_LEVEL", "WARNING").upper(), logging.WARNING),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global session management
session_manager = SessionManager()

# Track active WebSocket connections: client_id -> WebSocket
active_connections: dict[str, WebSocket] = {}

# Track session data stores: session_id -> SessionDataStore
session_data_stores: dict[str, SessionDataStore] = {}

# Track backend-assigned stream sequence numbers: session_id -> latest_seq
session_seq_counters: dict[str, int] = {}

# Track latest subscribe request epoch for each session to prevent stale
# long-running subscribe flows from applying after a newer interval/symbol switch.
session_subscribe_epochs: dict[str, int] = {}

# Track outbound sequence numbers sent from backend -> indicator agent:
# (indicator_agent_id, session_id) -> latest_seq
indicator_seq_counters: dict[tuple[str, str], int] = {}

# Track history_response chunk accumulation state:
# (agent_id, session_id, subscription_id) -> accumulator state
history_response_chunks: dict[tuple[str, str, str], dict[str, Any]] = {}

# Workspace persistence store
workspace_store: WorkspaceStore | None = None
trade_strategy_store: TradeStrategyStore | None = None

# Domain revision tracking for frontend/backend sync reconciliation.
domain_revisions: dict[str, int] = {
    "agent": 0,
    "overlay": 0,
    "trade": 0,
    "workspace": 0,
}

# Per-session trade revision and latest computed trade result cache.
session_trade_revisions: dict[str, int] = {}
latest_trade_results_by_session: dict[str, dict[str, Any]] = {}

# Last applied strategy state per session so backend can auto-recompute.
applied_trade_strategy_by_session: dict[str, dict[str, Any]] = {}

# Cooldown guard for indicator subscription recovery attempts.
indicator_recovery_last_attempt: dict[tuple[str, str], datetime] = {}

# CSV export job tracking (in-memory, per backend process).
csv_export_jobs: dict[str, dict[str, Any]] = {}
csv_export_tasks: dict[str, asyncio.Task] = {}
csv_export_root = Path(__file__).parent.parent / "data" / "exports"

# Runtime telemetry (Phase 0 baseline).
telemetry_counters: dict[str, int] = {
    "overlay_ingest_records_total": 0,
    "overlay_ingest_history_messages_total": 0,
    "overlay_ingest_update_messages_total": 0,
    "trade_recompute_total": 0,
    "trade_recompute_failed_total": 0,
}
telemetry_latency_ms: dict[str, deque[float]] = {
    "trade_recompute": deque(maxlen=1024),
}
overlay_ingest_samples: deque[tuple[float, int]] = deque(maxlen=4096)


def _increment_telemetry_counter(name: str, amount: int = 1) -> None:
    telemetry_counters[name] = int(telemetry_counters.get(name, 0)) + int(amount)


def _record_latency_ms(name: str, value_ms: float) -> None:
    bucket = telemetry_latency_ms.setdefault(name, deque(maxlen=1024))
    bucket.append(float(value_ms))


def _percentile_from_sorted(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = int(math.ceil((percentile / 100.0) * len(values))) - 1
    index = max(0, min(index, len(values) - 1))
    return float(values[index])


def _latency_summary(values: deque[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "avg_ms": 0.0}

    ordered = sorted(float(v) for v in values)
    total = sum(ordered)
    return {
        "count": float(len(ordered)),
        "p50_ms": round(_percentile_from_sorted(ordered, 50), 3),
        "p95_ms": round(_percentile_from_sorted(ordered, 95), 3),
        "max_ms": round(ordered[-1], 3),
        "avg_ms": round(total / len(ordered), 3),
    }


def _record_overlay_ingest(records_count: int) -> None:
    now = monotonic()
    overlay_ingest_samples.append((now, int(records_count)))
    _increment_telemetry_counter("overlay_ingest_records_total", int(records_count))


def _overlay_ingest_rps(window_seconds: float = 60.0) -> float:
    now = monotonic()
    while overlay_ingest_samples and now - overlay_ingest_samples[0][0] > window_seconds:
        overlay_ingest_samples.popleft()

    if not overlay_ingest_samples:
        return 0.0

    count = sum(sample_count for _, sample_count in overlay_ingest_samples)
    earliest = overlay_ingest_samples[0][0]
    elapsed = max(now - earliest, 1.0)
    return float(count) / elapsed


def _active_overlay_records_by_session() -> dict[str, int]:
    return {
        session_id: len(data_store.latest_non_ohlc_by_key)
        for session_id, data_store in session_data_stores.items()
    }


def _telemetry_snapshot() -> dict[str, Any]:
    return {
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "counters": {key: int(value) for key, value in telemetry_counters.items()},
        "overlay_ingest": {
            "rps_60s": round(_overlay_ingest_rps(60.0), 3),
            "warn_threshold_rps": OVERLAY_INGEST_WARN_RPS,
        },
        "latency_ms": {
            metric_name: _latency_summary(metric_values)
            for metric_name, metric_values in telemetry_latency_ms.items()
        },
        "active_overlay_records_by_session": _active_overlay_records_by_session(),
        "active_sessions": len(session_data_stores),
    }


def _parse_export_date(value: str, *, end_of_day: bool) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Date is required")

    try:
        if "T" in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed

        parsed_date = datetime.strptime(raw, "%Y-%m-%d").date()
        if end_of_day:
            return datetime(
                year=parsed_date.year,
                month=parsed_date.month,
                day=parsed_date.day,
                hour=23,
                minute=59,
                second=59,
                microsecond=999999,
                tzinfo=timezone.utc,
            )

        return datetime(
            year=parsed_date.year,
            month=parsed_date.month,
            day=parsed_date.day,
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise ValueError("Date must be ISO datetime or YYYY-MM-DD") from exc


def _add_months_utc(value: datetime, months: int) -> datetime:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    max_day = [
        31,
        29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ][month - 1]
    day = min(value.day, max_day)
    return value.replace(year=year, month=month, day=day)


def _iter_export_windows(start_at: datetime, end_at: datetime) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start_at
    while cursor <= end_at:
        next_cursor = _add_months_utc(cursor, EXPORT_CHUNK_MONTHS)
        window_end = min(end_at, next_cursor - timedelta(microseconds=1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(microseconds=1)
    return windows


def _parse_record_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed
    except ValueError:
        return None


def _record_in_range(record: dict[str, Any], start_at: datetime, end_at: datetime) -> bool:
    parsed = _parse_record_ts(record.get("ts"))
    if not parsed:
        return False
    return start_at <= parsed <= end_at


async def _wait_for_export_settle(
    data_store: SessionDataStore,
    min_delay_seconds: float,
    poll_seconds: float,
    timeout_seconds: float,
) -> None:
    await asyncio.sleep(min_delay_seconds)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    stable_ticks = 0
    last_signature: tuple[int, int, str] | None = None

    while datetime.now(timezone.utc) < deadline:
        signature = (
            len(data_store.latest_by_candle_id),
            len(data_store.latest_non_ohlc_by_key),
            str(data_store.last_event_ts or ""),
        )

        if signature == last_signature:
            stable_ticks += 1
        else:
            stable_ticks = 0

        if stable_ticks >= 2:
            return

        last_signature = signature
        await asyncio.sleep(poll_seconds)

    raise TimeoutError("Export settle timeout: data did not stabilize before timeout")


def _sorted_candles_for_push(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            str(record.get("ts") or ""),
            str(record.get("id") or ""),
            int(record.get("rev") or 0),
        ),
    )


def _format_export_csv(
    *,
    session_id: str,
    symbol: str,
    interval: str,
    candles: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
    output_path: Path,
) -> tuple[int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _safe_column_part(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "unknown"
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", text)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized.lower() or "unknown"

    def _short_agent_label(agent_id: Any) -> str:
        raw = str(agent_id or "").strip().lower()
        if not raw:
            return "unknown"

        prefixes = (
            "odin_indicator_agent__",
            "indicator_agent__",
            "odin_price_agent__",
            "price_agent__",
            "odin_",
        )
        for prefix in prefixes:
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break

        return _safe_column_part(raw)

    def _overlay_base_column(record: dict[str, Any]) -> str:
        agent_id = _short_agent_label(record.get("agent_id"))
        output_id = _safe_column_part(record.get("output_id"))
        schema = _safe_column_part(record.get("schema"))

        if output_id == "unknown":
            output_id = "default"

        if output_id == "default":
            return f"agent_{agent_id}_{schema}"

        return f"agent_{agent_id}_{output_id}_{schema}"

    def _csv_scalar(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return value
        return json.dumps(value, sort_keys=True)

    sorted_candles = sorted(
        candles,
        key=lambda candle: (
            str(candle.get("ts") or ""),
            str(candle.get("id") or ""),
            int(candle.get("rev") or 0),
        ),
    )

    overlays_by_ts: dict[str, list[dict[str, Any]]] = {}
    dynamic_columns: set[str] = set()

    for overlay in overlays:
        ts = str(overlay.get("ts") or "").strip()
        if not ts:
            continue

        overlays_by_ts.setdefault(ts, []).append(overlay)

        base = _overlay_base_column(overlay)
        for field_name in ("value", "upper", "lower", "center", "title", "severity", "action"):
            if overlay.get(field_name) is not None:
                dynamic_columns.add(f"{base}.{field_name}")

        metadata = overlay.get("metadata")
        if isinstance(metadata, dict):
            for metadata_key, metadata_value in metadata.items():
                if metadata_value is None:
                    continue
                dynamic_columns.add(f"{base}.meta.{_safe_column_part(metadata_key)}")

    fieldnames = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "bar_state",
        "symbol",
        "interval",
        *sorted(dynamic_columns),
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for candle in sorted_candles:
            ts = str(candle.get("ts") or "")
            row: dict[str, Any] = {
                "timestamp": ts,
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("volume"),
                "bar_state": candle.get("bar_state") or "",
                "symbol": symbol,
                "interval": interval,
            }

            for overlay in overlays_by_ts.get(ts, []):
                base = _overlay_base_column(overlay)
                for field_name in ("value", "upper", "lower", "center", "title", "severity", "action"):
                    if overlay.get(field_name) is not None:
                        row[f"{base}.{field_name}"] = _csv_scalar(overlay.get(field_name))

                metadata = overlay.get("metadata")
                if isinstance(metadata, dict):
                    for metadata_key, metadata_value in metadata.items():
                        if metadata_value is None:
                            continue
                        row[f"{base}.meta.{_safe_column_part(metadata_key)}"] = _csv_scalar(metadata_value)

            writer.writerow(row)

    return len(sorted_candles), len(overlays)


async def _run_csv_export_job(job_id: str) -> None:
    job = csv_export_jobs.get(job_id)
    if not job:
        return

    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    job["error"] = None

    export_session_id: str | None = None

    try:
        session_id = str(job["session_id"])
        symbol = str(job["symbol"])
        interval = str(job["interval"])
        start_at = _parse_export_date(str(job["start_date"]), end_of_day=False)
        end_at = _parse_export_date(str(job["end_date"]), end_of_day=True)

        session = session_manager.get_session(session_id)
        if not session:
            raise RuntimeError("Session not found")

        source_connection = agent_manager.get_connection(session.agent_id)
        if not source_connection:
            raise RuntimeError(f"Primary agent connection unavailable: {session.agent_id}")

        export_session_id = f"{session_id}::export::{job_id[:8]}"
        session_manager.create_session(
            session_id=export_session_id,
            client_id=session.client_id,
            agent_id=session.agent_id,
            symbol=symbol,
            interval=interval,
        )

        export_store = SessionDataStore(
            session_id=export_session_id,
            agent_id=session.agent_id,
            symbol=symbol,
            interval=interval,
        )
        export_days = max(1, math.ceil((end_at - start_at).total_seconds() / 86400))
        export_store.update_retention(export_days + 2, interval)
        session_data_stores[export_session_id] = export_store
        session_seq_counters[export_session_id] = -1

        indicator_connections: list[tuple[str, Any]] = []
        for indicator_agent in agent_manager.list_indicator_agents():
            if indicator_agent.config.agent_id == session.agent_id:
                continue
            indicator_connection = agent_manager.get_connection(indicator_agent.config.agent_id)
            if not indicator_connection:
                continue

            indicator_params = sanitize_indicator_params(
                indicator_agent.config.config_schema,
                indicator_agent.config.indicators,
                indicator_agent.config.selected_indicator_id,
            )
            subscribed = await indicator_connection.subscribe(
                session_id=export_session_id,
                symbol=symbol,
                interval=interval,
                params=indicator_params,
                force=True,
            )
            if subscribed:
                indicator_connections.append((indicator_agent.config.agent_id, indicator_connection))

        windows = _iter_export_windows(start_at, end_at)
        job["total_chunks"] = len(windows)
        job["completed_chunks"] = 0

        for index, (window_start, window_end) in enumerate(windows, start=1):
            job["current_chunk"] = index
            job["chunk_window"] = {
                "from": window_start.isoformat(),
                "to": window_end.isoformat(),
            }

            from_ts = source_connection._format_history_timestamp(window_start)
            to_ts = source_connection._format_history_timestamp(window_end)
            history_bars = await source_connection.fetch_history(symbol, from_ts, to_ts, interval)

            ingested_chunk: list[dict[str, Any]] = []
            for bar in history_bars:
                normalized = export_store.ingest_ohlc(bar)
                if normalized:
                    ingested_chunk.append(normalized)

            if ingested_chunk and indicator_connections:
                candles_for_push = [to_indicator_ohlc(candle) for candle in _sorted_candles_for_push(ingested_chunk)]
                await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)
                for _indicator_id, indicator_connection in indicator_connections:
                    await indicator_connection.send_history_push(
                        session_id=export_session_id,
                        symbol=symbol,
                        interval=interval,
                        candles=candles_for_push,
                    )

            await _wait_for_export_settle(
                export_store,
                min_delay_seconds=float(job["settle_min_delay_seconds"]),
                poll_seconds=float(job["settle_poll_seconds"]),
                timeout_seconds=float(job["settle_timeout_seconds"]),
            )

            job["completed_chunks"] = index

        candles_for_export = [
            candle for candle in export_store.get_canonical_candles() if _record_in_range(candle, start_at, end_at)
        ]
        overlays_for_export = [
            overlay for overlay in export_store.get_non_ohlc_records() if _record_in_range(overlay, start_at, end_at)
        ]

        output_path = csv_export_root / f"{job_id}.csv"
        candle_count, overlay_count = _format_export_csv(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            candles=candles_for_export,
            overlays=overlays_for_export,
            output_path=output_path,
        )

        job["status"] = "completed"
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
        job["download_file"] = str(output_path)
        job["candle_count"] = candle_count
        job["overlay_count"] = overlay_count
    except Exception as exc:
        job["status"] = "failed"
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
        job["error"] = str(exc)
        logger.exception("CSV export job failed (job_id=%s)", job_id)
    finally:
        if export_session_id:
            for connection in agent_manager.connections.values():
                try:
                    if export_session_id in connection.subscriptions:
                        await connection.unsubscribe(export_session_id)
                except Exception as exc:
                    logger.debug("Failed to unsubscribe export session %s: %s", export_session_id, exc)

            session_manager.delete_session(export_session_id)
            session_data_stores.pop(export_session_id, None)
            session_seq_counters.pop(export_session_id, None)
            session_subscribe_epochs.pop(export_session_id, None)
            session_trade_revisions.pop(export_session_id, None)
            latest_trade_results_by_session.pop(export_session_id, None)
            applied_trade_strategy_by_session.pop(export_session_id, None)
            clear_history_response_state_for_session(export_session_id)
            for key in [key for key in indicator_seq_counters.keys() if key[1] == export_session_id]:
                indicator_seq_counters.pop(key, None)

        csv_export_tasks.pop(job_id, None)


def _bump_domain_revision(domain: str) -> int:
    current = int(domain_revisions.get(domain, 0)) + 1
    domain_revisions[domain] = current
    return current


def _bump_trade_revision_for_session(session_id: str) -> int:
    next_rev = int(session_trade_revisions.get(session_id, 0)) + 1
    session_trade_revisions[session_id] = next_rev
    return next_rev


def _snapshot_domain_revisions() -> dict[str, int]:
    return {
        "agent": int(domain_revisions.get("agent", 0)),
        "overlay": int(domain_revisions.get("overlay", 0)),
        "trade": int(domain_revisions.get("trade", 0)),
        "workspace": int(domain_revisions.get("workspace", 0)),
    }


async def _send_json_to_client(client_id: str, payload: dict[str, Any]) -> None:
    websocket = active_connections.get(client_id)
    if not websocket:
        return
    try:
        await websocket.send_json(payload)
    except Exception as exc:
        logger.debug("Failed to send payload to client %s: %s", client_id, exc)


async def emit_state_event(
    *,
    event_name: str,
    domain: str,
    payload: dict[str, Any],
    session_id: str | None = None,
    target_client_id: str | None = None,
    causation_id: str | None = None,
) -> None:
    domain_revision = _bump_domain_revision(domain)

    message: dict[str, Any] = {
        "type": "state_event",
        "event_id": str(uuid4()),
        "event_name": event_name,
        "domain": domain,
        "revision": domain_revision,
        "server_revisions": _snapshot_domain_revisions(),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }

    if session_id:
        message["session_id"] = session_id
        if domain == "trade":
            message["session_revision"] = _bump_trade_revision_for_session(session_id)

    if causation_id:
        message["causation_id"] = causation_id

    if target_client_id:
        await _send_json_to_client(target_client_id, message)
        return

    if session_id:
        session = session_manager.get_session(session_id)
        if session:
            await _send_json_to_client(session.client_id, message)
        return

    for client_id in list(active_connections.keys()):
        await _send_json_to_client(client_id, message)


def get_workspace_store() -> WorkspaceStore:
    if workspace_store is None:
        raise HTTPException(status_code=500, detail="Workspace store not initialized")
    return workspace_store


def get_trade_strategy_store() -> TradeStrategyStore:
    if trade_strategy_store is None:
        raise HTTPException(status_code=500, detail="Trade strategy store not initialized")
    return trade_strategy_store


def buffer_replay_message(session, message: dict) -> dict:
    """Assign per-session monotonic seq and append message to replay buffer."""
    next_seq = session_seq_counters.get(session.session_id, -1) + 1
    session_seq_counters[session.session_id] = next_seq

    replay_message = copy.deepcopy(message)
    replay_message["seq"] = next_seq
    session.replay_buffer.append(replay_message)
    return replay_message


def next_indicator_seq(indicator_agent_id: str, session_id: str) -> int:
    """Return next monotonic seq for messages sent to a specific indicator/session."""
    key = (indicator_agent_id, session_id)
    next_seq = indicator_seq_counters.get(key, -1) + 1
    indicator_seq_counters[key] = next_seq
    return next_seq


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle"""
    global workspace_store, trade_strategy_store
    logger.info(f"🚀 Odin backend starting ({ACP_SPEC_VERSION})...")

    workspace_db_path = Path(__file__).parent.parent / "data" / "user_config.db"
    workspace_store = WorkspaceStore(workspace_db_path)
    trade_strategy_store = TradeStrategyStore(workspace_db_path)
    
    # Load agent configurations from YAML
    config_path = Path(__file__).parent.parent.parent / "overlay_agents.yaml"
    logger.info(f"📂 Loading agent configs from: {config_path}")
    agent_manager.load_from_yaml(config_path)
    agent_manager.config_file_path = config_path  # Store path for persistence
    
    # Set up message callbacks for ACP routing and reconnect re-bootstrap
    agent_manager.on_agent_message = route_agent_message
    agent_manager.on_rebootstrap = rebootstrap_indicator_subscription
    
    # Start WebSocket connections to all agents
    logger.info("🔌 Connecting to agents...")
    await agent_manager.start_all_connections()
    
    logger.info("📡 WebSocket endpoint available at: ws://localhost:8001/ws")
    yield
    
    # Cleanup
    logger.info("🛑 Stopping agent connections...")
    await agent_manager.stop_all_connections()
    logger.info("🛑 Odin backend shutting down...")


app = FastAPI(
    title="Odin Backend",
    description="Trading platform backend - ACP v0.4.2 session router",
    version=ACP_API_VERSION,
    lifespan=lifespan,
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "odin-backend",
        "version": ACP_API_VERSION,
        "status": "running",
        "acp_version": ACP_API_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(active_connections),
        "active_sessions": len(session_manager.list_all_sessions()),
        "agents_loaded": len(agent_manager.list_agents()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/agents")
async def list_agents():
    """Get list of all configured agents"""
    return {
        "agents": agent_manager.list_agents_for_frontend(),
        "count": len(agent_manager.list_agents()),
    }


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get details for a specific agent"""
    agent = agent_manager.get_agent_for_frontend(agent_id)
    if not agent:
        return {"error": "Agent not found"}, 404
    return agent


@app.get("/api/runtime/telemetry")
async def get_runtime_telemetry():
    """Phase 0 runtime telemetry snapshot for ingest/load baselining."""
    snapshot = _telemetry_snapshot()
    rps = float(snapshot.get("overlay_ingest", {}).get("rps_60s", 0.0))
    if rps >= OVERLAY_INGEST_WARN_RPS:
        logger.warning(
            "[Telemetry] overlay ingest rate high: %.2f rps (threshold=%.2f)",
            rps,
            OVERLAY_INGEST_WARN_RPS,
        )
    return snapshot


async def fetch_agent_metadata(agent_url: str) -> dict:
    normalized_url = agent_url.rstrip("/")
    metadata_url = f"{normalized_url}/metadata"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(metadata_url)
        response.raise_for_status()
    metadata = response.json()

    discovered_spec_version = metadata.get("spec_version")
    if discovered_spec_version not in COMPATIBLE_ACP_SPEC_VERSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Incompatible spec_version: {discovered_spec_version} "
                f"(expected one of {sorted(COMPATIBLE_ACP_SPEC_VERSIONS)})"
            ),
        )

    if metadata.get("agent_type") not in {"price", "indicator", "event"}:
        raise HTTPException(status_code=400, detail="Invalid agent_type in metadata")

    outputs = metadata.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise HTTPException(status_code=400, detail="Metadata outputs[] is required")

    transport_limits = metadata.get("transport_limits")
    if not isinstance(transport_limits, dict):
        raise HTTPException(status_code=400, detail="Metadata transport_limits is required")

    max_records_per_chunk = int(transport_limits.get("max_records_per_chunk") or 0)
    max_websocket_message_bytes = int(transport_limits.get("max_websocket_message_bytes") or 0)
    chunk_timeout_seconds = int(
        transport_limits.get("chunk_timeout_seconds") or DEFAULT_CHUNK_TIMEOUT_SECONDS
    )

    if max_records_per_chunk < 1000 or max_records_per_chunk > 10000:
        raise HTTPException(status_code=400, detail="transport_limits.max_records_per_chunk must be 1000-10000")

    if max_websocket_message_bytes < 1048576:
        raise HTTPException(status_code=400, detail="transport_limits.max_websocket_message_bytes must be >= 1048576")

    if chunk_timeout_seconds < 1:
        raise HTTPException(status_code=400, detail="transport_limits.chunk_timeout_seconds must be >= 1")

    metadata["transport_limits"] = {
        "max_records_per_chunk": max_records_per_chunk,
        "max_websocket_message_bytes": max_websocket_message_bytes,
        "chunk_timeout_seconds": chunk_timeout_seconds,
    }

    if metadata.get("agent_type") == "indicator":
        indicators = metadata.get("indicators")
        if not isinstance(indicators, list) or not indicators:
            raise HTTPException(status_code=400, detail="Indicator agents must expose indicators[]")

    return metadata


def clear_history_response_state_for_session(session_id: str) -> None:
    keys_to_remove = [key for key in history_response_chunks.keys() if key[1] == session_id]
    for key in keys_to_remove:
        history_response_chunks.pop(key, None)


async def send_protocol_error_to_agent(
    agent_id: str,
    session_id: str,
    subscription_id: str,
    code: str,
    message_text: str,
) -> None:
    connection = agent_manager.get_connection(agent_id)
    if not connection:
        return

    await connection.send_message(
        {
            "type": "error",
            "spec_version": ACP_SPEC_VERSION,
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": agent_id,
            "code": code,
            "message": message_text,
            "retryable": False,
        }
    )


async def rebootstrap_indicator_subscription(indicator_agent_id: str, session_id: str) -> None:
    """ACP-0.4.0 reconnect contract: subscribe + full chunked history_push."""
    indicator_connection = agent_manager.get_connection(indicator_agent_id)
    if not indicator_connection:
        return

    subscription = indicator_connection.subscriptions.get(session_id)
    if not subscription:
        return

    data_store = session_data_stores.get(session_id)
    if not data_store:
        return

    canonical_candles = data_store.get_canonical_candles()
    candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]

    await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)

    await indicator_connection.send_history_push(
        session_id=session_id,
        symbol=str(subscription.get("symbol") or ""),
        interval=str(subscription.get("interval") or ""),
        candles=candles_for_indicator,
    )


async def recover_indicator_subscription(indicator_agent_id: str, session_id: str) -> bool:
    """
    Recover indicator subscription state when agent rejects history_push with SUBSCRIPTION_NOT_FOUND.

    This can occur during transient ordering/race conditions inside indicator agents.
    """
    recovery_key = (indicator_agent_id, session_id)
    now = datetime.now(timezone.utc)
    last_attempt = indicator_recovery_last_attempt.get(recovery_key)
    if last_attempt and (now - last_attempt).total_seconds() < 5:
        logger.info(
            "[Recovery] Skipping rapid retry for indicator=%s session=%s",
            indicator_agent_id,
            session_id,
        )
        return

    indicator_recovery_last_attempt[recovery_key] = now

    indicator_connection = agent_manager.get_connection(indicator_agent_id)
    if not indicator_connection:
        return False

    subscription = indicator_connection.subscriptions.get(session_id)
    if not subscription:
        logger.warning(
            "[Recovery] No local subscription found for indicator=%s session=%s",
            indicator_agent_id,
            session_id,
        )
        return False

    symbol = str(subscription.get("symbol") or "")
    interval = str(subscription.get("interval") or "")
    params = sanitize_indicator_params(
        subscription.get("params") or {},
        indicator_connection.agent.config.indicators,
        indicator_connection.agent.config.selected_indicator_id,
    )
    if not symbol or not interval:
        return False

    subscribe_ok = await indicator_connection.subscribe(
        session_id=session_id,
        symbol=symbol,
        interval=interval,
        params=params,
        force=True,
    )
    if not subscribe_ok:
        logger.warning(
            "[Recovery] Re-subscribe failed for indicator=%s session=%s",
            indicator_agent_id,
            session_id,
        )
        return False

    data_store = session_data_stores.get(session_id)
    if not data_store:
        return False

    canonical_candles = data_store.get_canonical_candles()
    if not canonical_candles:
        return True

    candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]
    await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)

    send_ok = await indicator_connection.send_history_push(
        session_id=session_id,
        symbol=symbol,
        interval=interval,
        candles=candles_for_indicator,
    )
    if not send_ok:
        return False
    logger.info(
        "[Recovery] Recovered indicator subscription for %s (session=%s, candles=%s)",
        indicator_agent_id,
        session_id,
        len(candles_for_indicator),
    )
    return True


@app.post("/api/agents/discover")
async def discover_agent(request: DiscoverAgentRequest):
    try:
        metadata = await fetch_agent_metadata(request.agent_url)
        return {
            "agent_url": request.agent_url.rstrip("/"),
            "metadata": metadata,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch metadata: {exc}") from exc


@app.post("/api/agents")
async def add_agent(request: AddAgentRequest):
    try:
        metadata = await fetch_agent_metadata(request.agent_url)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch metadata: {exc}") from exc

    base_agent_id = metadata["agent_id"]
    agent_type = metadata["agent_type"]
    selected_indicator = None
    outputs = metadata.get("outputs") or []
    description = metadata.get("description", "")
    selected_indicator_id: str | None = None

    final_agent_id = base_agent_id
    final_name = metadata["agent_name"]

    if agent_type == "indicator":
        if not request.indicator_id:
            raise HTTPException(status_code=400, detail="indicator_id is required for indicator agents")
        indicators = metadata.get("indicators") or []
        selected_indicator = next((item for item in indicators if item.get("indicator_id") == request.indicator_id), None)
        if not selected_indicator:
            raise HTTPException(status_code=400, detail=f"Unknown indicator_id: {request.indicator_id}")
        selected_indicator_id = request.indicator_id
        indicator_base_agent_id = f"{base_agent_id}__{request.indicator_id}"
        final_agent_id, instance_index = next_available_indicator_instance(indicator_base_agent_id)
        final_name = f"{metadata['agent_name']} - {selected_indicator.get('name', request.indicator_id)}"
        if instance_index > 1:
            final_name = f"{final_name} ({instance_index})"
        outputs = selected_indicator.get("outputs") or outputs
        description = selected_indicator.get("description") or description

    runtime_config = sanitize_indicator_params(
        request.params or {},
        metadata.get("indicators") or [],
        selected_indicator_id,
    )
    # Forward all UI-managed keys from incoming params so they survive sanitize_indicator_params.
    # See UI_MANAGED_INDICATOR_CONFIG_KEYS in models.py (line_color, visible, force_subgraph, etc.).
    _incoming_params = request.params or {}
    _ui_keys = {
        k: _incoming_params[k]
        for k in UI_MANAGED_INDICATOR_CONFIG_KEYS
        if k in _incoming_params and _incoming_params[k] is not None
    }
    stored_config = normalize_indicator_config(
        agent_id=final_agent_id,
        config_schema={
            **runtime_config,
            **_ui_keys,
        },
        indicators=metadata.get("indicators") or [],
        outputs=outputs,
        selected_indicator_id=selected_indicator_id,
    )

    agent_config = AgentConfig(
        spec_version=ACP_SPEC_VERSION,
        agent_url=request.agent_url.rstrip("/"),
        agent_id=final_agent_id,
        agent_name=final_name,
        agent_version=metadata["agent_version"],
        description=description,
        agent_type=agent_type,
        config_schema=stored_config,
        outputs=outputs,
        indicators=metadata.get("indicators") or [],
        selected_indicator_id=selected_indicator_id,
        transport_limits=metadata.get("transport_limits") or {},
    )

    agent = agent_manager.add_or_update_agent(agent_config)

    existing_connection = agent_manager.get_connection(final_agent_id)
    if not existing_connection:
        from app.agent_connection import AgentConnection

        connection = AgentConnection(
            agent=agent,
            on_message=route_agent_message,
            on_rebootstrap=rebootstrap_indicator_subscription,
        )
        agent_manager.add_connection(final_agent_id, connection)
        await connection.start()
    else:
        connection = existing_connection

    if agent_type == "indicator":
        active_sessions = session_manager.list_all_sessions()
        for session in active_sessions:
            session_id = session.session_id
            symbol = session.symbol
            interval = session.interval

            subscribe_result = await connection.subscribe(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                params=runtime_config,
            )

            if not subscribe_result:
                continue

            data_store = session_data_stores.get(session_id)
            if not data_store:
                continue

            canonical_candles = data_store.get_canonical_candles()
            if not canonical_candles:
                continue

            candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]

            await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)

            await connection.send_history_push(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                candles=candles_for_indicator,
            )

    # Persist agents to YAML after adding
    if agent_manager.config_file_path:
        agent_manager.persist_agents_to_yaml(agent_manager.config_file_path)

    await emit_state_event(
        event_name="agent.config.changed",
        domain="agent",
        payload={
            "agent_id": final_agent_id,
            "action": "added",
        },
    )

    return {
        "agent": agent.to_frontend_format(),
        "selected_indicator": selected_indicator,
        "params": request.params,
    }


@app.patch("/api/agents/{agent_id}")
async def update_agent(agent_id: str, request: UpdateAgentRequest):
    agent = agent_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    selected_indicator_id = agent.config.selected_indicator_id or infer_selected_indicator_id(
        agent_id=agent.config.agent_id,
        indicators=agent.config.indicators,
        outputs=agent.config.outputs,
        config_schema=agent.config.config_schema,
        selected_indicator_id=agent.config.selected_indicator_id,
    )
    existing_runtime_config = sanitize_indicator_params(
        agent.config.config_schema,
        agent.config.indicators,
        selected_indicator_id,
    )
    request_runtime_config = sanitize_indicator_params(
        request.params or {},
        agent.config.indicators,
        selected_indicator_id,
    )
    updated_runtime_config = {
        **existing_runtime_config,
        **request_runtime_config,
    }
    # Forward all UI-managed keys: preserve existing values, allow request to override.
    # This ensures keys like visible, force_subgraph, line_color survive the
    # sanitize_indicator_params pipeline which only keeps schema-declared keys.
    _existing_ui_keys = {
        k: agent.config.config_schema[k]
        for k in UI_MANAGED_INDICATOR_CONFIG_KEYS
        if k in agent.config.config_schema and agent.config.config_schema[k] is not None
    }
    _request_ui_keys = {
        k: (request.params or {})[k]
        for k in UI_MANAGED_INDICATOR_CONFIG_KEYS
        if k in (request.params or {}) and (request.params or {})[k] is not None
    }
    updated_config = normalize_indicator_config(
        agent_id=agent.config.agent_id,
        config_schema={
            **updated_runtime_config,
            **_existing_ui_keys,
            **_request_ui_keys,
        },
        indicators=agent.config.indicators,
        outputs=agent.config.outputs,
        selected_indicator_id=selected_indicator_id,
    )

    updated_agent_config = AgentConfig(
        spec_version=agent.config.spec_version,
        agent_url=agent.config.agent_url,
        agent_id=agent.config.agent_id,
        agent_name=request.agent_name or agent.config.agent_name,
        agent_version=agent.config.agent_version,
        description=agent.config.description,
        agent_type=agent.config.agent_type,
        config_schema=updated_config,
        outputs=agent.config.outputs,
        indicators=agent.config.indicators,
        selected_indicator_id=selected_indicator_id,
        transport_limits=agent.config.transport_limits,
    )

    updated_agent = agent_manager.add_or_update_agent(updated_agent_config)

    connection = agent_manager.get_connection(agent_id)
    affected_sessions: list[str] = []
    if connection and updated_agent.config.agent_type == "indicator":
        subscriptions = list(connection.subscriptions.items())
        for session_id, subscription in subscriptions:
            symbol = str(subscription.get("symbol") or "")
            interval = str(subscription.get("interval") or "")
            if not symbol or not interval:
                continue

            affected_sessions.append(session_id)

            await connection.subscribe(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                params=updated_runtime_config,
            )

            if session_id in session_data_stores:
                canonical_candles = session_data_stores[session_id].get_canonical_candles()
                if canonical_candles:
                    candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]

                    await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)

                    await connection.send_history_push(
                        session_id=session_id,
                        symbol=symbol,
                        interval=interval,
                        candles=candles_for_indicator,
                    )

    # Persist agents to YAML after updating
    if agent_manager.config_file_path:
        agent_manager.persist_agents_to_yaml(agent_manager.config_file_path)

    await emit_state_event(
        event_name="agent.config.changed",
        domain="agent",
        payload={
            "agent_id": agent_id,
            "action": "updated",
            "params": updated_config,
        },
    )

    for session_id in sorted(set(affected_sessions)):
        await emit_state_event(
            event_name="trade.results.invalidated",
            domain="trade",
            session_id=session_id,
            payload={
                "session_id": session_id,
                "reason": "indicator_config_changed",
                "agent_id": agent_id,
            },
        )

        strategy = applied_trade_strategy_by_session.get(session_id)
        data_store = session_data_stores.get(session_id)
        if not strategy or not data_store:
            continue

        try:
            recompute_started = perf_counter()
            result = evaluate_strategy(
                session_id=session_id,
                strategy_name=str(strategy.get("strategy_name") or "Unsaved Strategy"),
                long_entry_rule=str(strategy.get("long_entry_rule") or ""),
                long_exit_rules=list(strategy.get("long_exit_rules") or []),
                short_entry_rule=str(strategy.get("short_entry_rule") or ""),
                short_exit_rules=list(strategy.get("short_exit_rules") or []),
                data_store=data_store,
            )
            recompute_ms = (perf_counter() - recompute_started) * 1000.0
            _increment_telemetry_counter("trade_recompute_total")
            _record_latency_ms("trade_recompute", recompute_ms)
            if recompute_ms >= TRADE_RECOMPUTE_WARN_MS:
                logger.warning(
                    "[Telemetry] auto trade recompute latency high: %.2fms session=%s strategy=%s threshold=%.2fms",
                    recompute_ms,
                    session_id,
                    strategy.get("strategy_name"),
                    TRADE_RECOMPUTE_WARN_MS,
                )
            latest_trade_results_by_session[session_id] = result
            await emit_state_event(
                event_name="trade.results.recomputed",
                domain="trade",
                session_id=session_id,
                payload={
                    "session_id": session_id,
                    "strategy_name": strategy.get("strategy_name"),
                    "result": result,
                },
            )
        except Exception as exc:
            _increment_telemetry_counter("trade_recompute_failed_total")
            logger.warning("Auto-recompute failed for session %s after agent update: %s", session_id, exc)

    return {"agent": updated_agent.to_frontend_format()}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    agent = agent_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.config.agent_type != "indicator":
        raise HTTPException(status_code=400, detail="Only indicator agents can be removed at runtime")

    connection = agent_manager.get_connection(agent_id)
    if connection:
        for session_id in list(connection.subscriptions.keys()):
            await connection.unsubscribe(session_id)
        await connection.stop()
        agent_manager.remove_connection(agent_id)

    agent_manager.agents.pop(agent_id, None)

    # Persist agents to YAML after deleting
    if agent_manager.config_file_path:
        agent_manager.persist_agents_to_yaml(agent_manager.config_file_path)

    await emit_state_event(
        event_name="agent.config.changed",
        domain="agent",
        payload={
            "agent_id": agent_id,
            "action": "deleted",
        },
    )

    return {"deleted": agent_id}


@app.get("/api/workspaces")
async def list_workspaces():
    store = get_workspace_store()
    return {
        "workspaces": store.list_workspaces(),
        "active_workspace": store.get_active_workspace(),
    }


@app.get("/api/workspaces/{workspace_name}")
async def get_workspace(workspace_name: str):
    store = get_workspace_store()
    workspace = store.get_workspace(workspace_name)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


@app.put("/api/workspaces/{workspace_name}")
async def upsert_workspace(workspace_name: str, request: UpsertWorkspaceRequest):
    name = workspace_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workspace name is required")

    store = get_workspace_store()
    workspace = store.upsert_workspace(name, request.state, request.schema_version)
    await emit_state_event(
        event_name="workspace.changed",
        domain="workspace",
        payload={"workspace": name, "action": "saved"},
    )
    return workspace


@app.post("/api/workspaces/{workspace_name}/activate")
async def activate_workspace(workspace_name: str):
    store = get_workspace_store()
    workspace = store.get_workspace(workspace_name)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    store.set_active_workspace(workspace_name)
    await emit_state_event(
        event_name="workspace.changed",
        domain="workspace",
        payload={"workspace": workspace_name, "action": "activated"},
    )
    return {
        "active_workspace": workspace_name,
        "workspace": workspace,
    }


@app.delete("/api/workspaces/{workspace_name}")
async def delete_workspace(workspace_name: str):
    store = get_workspace_store()
    existing = store.get_workspace(workspace_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    all_workspaces = store.list_workspaces()
    if len(all_workspaces) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only workspace")

    deleted = store.delete_workspace(workspace_name)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete workspace")

    active_workspace = store.get_active_workspace()
    if active_workspace is None:
        remaining = store.list_workspaces()
        if remaining:
            store.set_active_workspace(remaining[0]["name"])
            active_workspace = remaining[0]["name"]

    await emit_state_event(
        event_name="workspace.changed",
        domain="workspace",
        payload={"workspace": workspace_name, "action": "deleted", "active_workspace": active_workspace},
    )

    return {
        "deleted": workspace_name,
        "active_workspace": active_workspace,
    }


@app.get("/api/sessions/{session_id}/variables")
async def get_session_variables(session_id: str):
    """
    Get all available data variables for a session.
    
    Returns OHLCV fields (OPEN, HIGH, LOW, CLOSE, VOLUME) plus all active indicator outputs.
    For multi-output indicators (bands, etc.), each field is returned as a separate variable.
    """
    # Check if session exists
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check if we have a data store for this session
    data_store = session_data_stores.get(session_id)
    if not data_store:
        raise HTTPException(status_code=404, detail="Session data store not found")
    
    variables: list[Variable] = []
    
    # Add OHLCV base variables
    ohlcv_fields = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
    for field in ohlcv_fields:
        variables.append(Variable(
            name=field,
            type="ohlcv",
            schema="number",
            agent_id=None,
            output_id=None
        ))
    
    # Get all indicator agents that have data in this session
    # The data_store.latest_non_ohlc_by_key is keyed by (agent_id, record_id)
    indicator_agent_ids = set()
    for (agent_id, _) in data_store.latest_non_ohlc_by_key.keys():
        indicator_agent_ids.add(agent_id)
    
    # For each indicator agent, get its outputs from metadata
    for agent_id in indicator_agent_ids:
        agent = agent_manager.get_agent(agent_id)
        if not agent:
            continue
        
        agent_name = agent.config.agent_name
        outputs = agent.config.outputs
        
        for output in outputs:
            output_schema = output.get("schema", "line")
            output_id = output.get("output_id", "")
            
            # For simple schemas, create one primary variable
            if output_schema in ["line", "histogram", "event", "forecast"]:
                var_name = build_variable_name(agent_name, output)
                variables.append(Variable(
                    name=var_name,
                    type="indicator",
                    schema=output_schema,
                    agent_id=agent_id,
                    output_id=output_id
                ))
            
            # For band schema, create separate variables for each field
            elif output_schema == "band":
                label = output.get("label", output_id)
                for field in ["upper", "lower", "center"]:
                    var_name = f"{agent_name}:{label}:{field}"
                    variables.append(Variable(
                        name=var_name,
                        type="indicator",
                        schema="band",
                        agent_id=agent_id,
                        output_id=output_id
                    ))

            # For area schema, expose upper/lower bounds as independent variables
            elif output_schema == "area":
                for field in ["upper", "lower"]:
                    var_name = build_area_field_variable_name(agent_name, output, field)
                    variables.append(Variable(
                        name=var_name,
                        type="indicator",
                        schema="area",
                        agent_id=agent_id,
                        output_id=output_id
                    ))

                # Also expose numeric metadata fields for area outputs.
                # Useful for rich area indicators that emit additional context
                # (e.g. directional state, phase, confidence) in record.metadata.
                metadata_numeric_keys: set[str] = set()
                for (record_agent_id, _record_id), record in data_store.latest_non_ohlc_by_key.items():
                    if record_agent_id != agent_id:
                        continue

                    record_output_id = record.get("output_id")
                    if output_id and record_output_id and str(record_output_id) != str(output_id):
                        continue

                    metadata = record.get("metadata")
                    if not isinstance(metadata, dict):
                        continue

                    for metadata_key, metadata_value in metadata.items():
                        if isinstance(metadata_value, bool):
                            continue
                        if isinstance(metadata_value, (int, float)):
                            metadata_numeric_keys.add(str(metadata_key))

                for metadata_key in sorted(metadata_numeric_keys):
                    variables.append(Variable(
                        name=build_area_metadata_variable_name(agent_name, output, metadata_key),
                        type="indicator",
                        schema="area",
                        agent_id=agent_id,
                        output_id=output_id
                    ))
            
            # For ohlc schema (though unlikely for indicators)
            elif output_schema == "ohlc":
                label = output.get("label", output_id)
                for field in ["open", "high", "low", "close", "volume"]:
                    var_name = f"{agent_name}:{label}:{field}"
                    variables.append(Variable(
                        name=var_name,
                        type="indicator",
                        schema="ohlc",
                        agent_id=agent_id,
                        output_id=output_id
                    ))
    
    return {
        "session_id": session_id,
        "variables": [v.model_dump() for v in variables],
        "count": len(variables)
    }


@app.post("/api/sessions/{session_id}/exports/csv")
async def create_csv_export_job(session_id: str, request: CreateCsvExportRequest):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    data_store = session_data_stores.get(session_id)
    if not data_store:
        raise HTTPException(status_code=404, detail="Session data store not found")

    interval = str(request.interval or "").strip() or session.interval
    if interval != session.interval:
        raise HTTPException(
            status_code=400,
            detail=f"Export interval must match active session interval ({session.interval})",
        )

    try:
        start_at = _parse_export_date(request.start_date, end_of_day=False)
        end_at = _parse_export_date(request.end_date, end_of_day=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if start_at > end_at:
        raise HTTPException(status_code=400, detail="start_date must be before or equal to end_date")

    if not data_store.get_canonical_candles():
        raise HTTPException(status_code=409, detail="Session has no candle data yet")

    job_id = str(uuid4())
    csv_export_jobs[job_id] = {
        "job_id": job_id,
        "session_id": session_id,
        "symbol": session.symbol,
        "interval": interval,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "total_chunks": 0,
        "completed_chunks": 0,
        "current_chunk": 0,
        "chunk_window": None,
        "settle_min_delay_seconds": request.settle_min_delay_seconds,
        "settle_poll_seconds": request.settle_poll_seconds,
        "settle_timeout_seconds": request.settle_timeout_seconds,
        "download_file": None,
        "candle_count": 0,
        "overlay_count": 0,
    }

    csv_export_tasks[job_id] = asyncio.create_task(_run_csv_export_job(job_id))

    return {
        "job_id": job_id,
        "status": "queued",
        "session_id": session_id,
        "symbol": session.symbol,
        "interval": interval,
        "start_date": request.start_date,
        "end_date": request.end_date,
    }


@app.get("/api/sessions/{session_id}/exports/csv/{job_id}")
async def get_csv_export_job(session_id: str, job_id: str):
    job = csv_export_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    if str(job.get("session_id")) != session_id:
        raise HTTPException(status_code=404, detail="Export job not found for session")

    return {
        "job_id": job_id,
        "session_id": session_id,
        "symbol": job.get("symbol"),
        "interval": job.get("interval"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
        "start_date": job.get("start_date"),
        "end_date": job.get("end_date"),
        "total_chunks": job.get("total_chunks"),
        "completed_chunks": job.get("completed_chunks"),
        "current_chunk": job.get("current_chunk"),
        "chunk_window": job.get("chunk_window"),
        "candle_count": job.get("candle_count"),
        "overlay_count": job.get("overlay_count"),
        "ready": job.get("status") == "completed" and bool(job.get("download_file")),
    }


@app.get("/api/sessions/{session_id}/exports/csv/{job_id}/download")
async def download_csv_export_job(session_id: str, job_id: str):
    job = csv_export_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    if str(job.get("session_id")) != session_id:
        raise HTTPException(status_code=404, detail="Export job not found for session")

    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Export job is not completed")

    file_path_raw = str(job.get("download_file") or "").strip()
    if not file_path_raw:
        raise HTTPException(status_code=404, detail="Export file missing")

    file_path = Path(file_path_raw)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file missing")

    filename = f"odin_export_{session_id}_{job.get('start_date')}_{job.get('end_date')}.csv"
    return FileResponse(path=file_path, media_type="text/csv", filename=filename)


@app.post("/api/sessions/{session_id}/research/evaluate")
async def evaluate_session_research_expression(session_id: str, request: EvaluateResearchExpressionRequest):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    data_store = session_data_stores.get(session_id)
    if not data_store:
        raise HTTPException(status_code=404, detail="Session data store not found")

    try:
        result = evaluate_research_expression(
            session_id=session_id,
            expression=request.expression,
            output_schema=request.output_schema,
            data_store=data_store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@app.get("/api/sessions/{session_id}/trade-strategies")
async def list_trade_strategies(session_id: str):
    store = get_trade_strategy_store()
    strategies = store.list_strategies(session_id)
    return {
        "session_id": session_id,
        "strategies": strategies,
        "count": len(strategies),
    }


@app.get("/api/sessions/{session_id}/trade-strategies/{strategy_name}")
async def get_trade_strategy(session_id: str, strategy_name: str):
    store = get_trade_strategy_store()
    strategy = store.get_strategy(session_id, strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@app.put("/api/sessions/{session_id}/trade-strategies/{strategy_name}")
async def upsert_trade_strategy(session_id: str, strategy_name: str, request: UpsertTradeStrategyRequest):
    long_entry_rules, long_exit_rules, short_entry_rules, short_exit_rules = _normalize_strategy_rules_payload(
        request.model_dump()
    )

    validation = validate_strategy_v2(
        long_entry_rules=long_entry_rules,
        long_exit_rules=long_exit_rules,
        short_entry_rules=short_entry_rules,
        short_exit_rules=short_exit_rules,
    )
    if not validation.valid:
        raise HTTPException(status_code=400, detail={"valid": False, "errors": validation.errors})

    store = get_trade_strategy_store()
    saved = store.upsert_strategy(
        session_id=session_id,
        name=strategy_name,
        description=request.description,
        long_entry_rules=long_entry_rules,
        long_exit_rules=long_exit_rules,
        short_entry_rules=short_entry_rules,
        short_exit_rules=short_exit_rules,
    )
    return saved


@app.delete("/api/sessions/{session_id}/trade-strategies/{strategy_name}")
async def delete_trade_strategy(session_id: str, strategy_name: str):
    store = get_trade_strategy_store()
    deleted = store.delete_strategy(session_id, strategy_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Strategy not found")

    return {
        "deleted": strategy_name,
        "session_id": session_id,
    }


@app.post("/api/sessions/{session_id}/trade-strategies/validate")
async def validate_trade_strategy(session_id: str, request: ValidateTradeStrategyRequest):
    long_entry_rules, long_exit_rules, short_entry_rules, short_exit_rules = _normalize_strategy_rules_payload(
        request.model_dump()
    )

    validation = validate_strategy_v2(
        long_entry_rules=long_entry_rules,
        long_exit_rules=long_exit_rules,
        short_entry_rules=short_entry_rules,
        short_exit_rules=short_exit_rules,
    )
    return {
        "session_id": session_id,
        "valid": validation.valid,
        "errors": validation.errors,
    }


@app.post("/api/sessions/{session_id}/trade-strategies/apply")
async def apply_trade_strategy(session_id: str, request: ApplyTradeStrategyRequest):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=409, detail="Session not ready. Wait for stream connection and retry apply.")

    data_store = session_data_stores.get(session_id)
    if not data_store:
        raise HTTPException(status_code=404, detail="Session data store not found")

    strategy_name = (request.strategy_name or "").strip()
    long_entry_rules, long_exit_rules, short_entry_rules, short_exit_rules = _normalize_strategy_rules_payload(
        request.model_dump()
    )

    if strategy_name and not long_entry_rules and not long_exit_rules and not short_entry_rules and not short_exit_rules:
        store = get_trade_strategy_store()
        saved = store.get_strategy(session_id, strategy_name)
        if not saved:
            raise HTTPException(status_code=404, detail="Strategy not found")
        long_entry_rules, long_exit_rules, short_entry_rules, short_exit_rules = _normalize_strategy_rules_payload(saved)

    if not strategy_name:
        strategy_name = "Unsaved Strategy"

    if not long_entry_rules and not short_entry_rules:
        raise HTTPException(
            status_code=400,
            detail="Provide strategy rules (long and/or short), or provide strategy_name for a saved strategy",
        )

    recompute_started = perf_counter()
    try:
        result = evaluate_strategy(
            session_id=session_id,
            strategy_name=strategy_name,
            long_entry_rules=long_entry_rules,
            long_exit_rules=long_exit_rules,
            short_entry_rules=short_entry_rules,
            short_exit_rules=short_exit_rules,
            data_store=data_store,
        )
    except ValueError as exc:
        _increment_telemetry_counter("trade_recompute_failed_total")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recompute_ms = (perf_counter() - recompute_started) * 1000.0
    _increment_telemetry_counter("trade_recompute_total")
    _record_latency_ms("trade_recompute", recompute_ms)
    if recompute_ms >= TRADE_RECOMPUTE_WARN_MS:
        logger.warning(
            "[Telemetry] trade recompute latency high: %.2fms session=%s strategy=%s threshold=%.2fms",
            recompute_ms,
            session_id,
            strategy_name,
            TRADE_RECOMPUTE_WARN_MS,
        )

    performance = result.get("performance") if isinstance(result, dict) else None
    logger.info(
        "[TradeApply] Returning apply result session=%s strategy=%s markers=%s has_performance=%s trades=%s total_pl=%s win_rate=%s curve_points=%s",
        session_id,
        strategy_name,
        result.get("marker_count") if isinstance(result, dict) else None,
        bool(performance),
        performance.get("total_trades") if isinstance(performance, dict) else None,
        performance.get("total_pl") if isinstance(performance, dict) else None,
        performance.get("win_rate") if isinstance(performance, dict) else None,
        len(performance.get("equity_curve", [])) if isinstance(performance, dict) else None,
    )

    latest_trade_results_by_session[session_id] = result
    applied_trade_strategy_by_session[session_id] = {
        "strategy_name": strategy_name,
        "long_entry_rules": long_entry_rules,
        "long_exit_rules": long_exit_rules,
        "short_entry_rules": short_entry_rules,
        "short_exit_rules": short_exit_rules,
    }

    await emit_state_event(
        event_name="trade.results.recomputed",
        domain="trade",
        session_id=session_id,
        payload={
            "session_id": session_id,
            "strategy_name": strategy_name,
            "result": result,
        },
    )

    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ACP v0.4.0 WebSocket endpoint for frontend connections.
    
    Protocol flow:
    1. Client connects -> server sends connection_ready with client_id
    2. Client sends subscribe_request with session_id, agent_id, symbol, interval
    3. Server creates session, routes subscribe to agent via AgentConnection
    4. Agent responds with data/heartbeat/error messages (includes session_id)
    5. Server routes messages to the specific session's WebSocket
    6. Client disconnects -> server cleans up all sessions for that client
    """
    await websocket.accept()
    
    # Generate unique client_id for this WebSocket connection
    client_id = str(id(websocket))
    active_connections[client_id] = websocket
    
    logger.info(f"✅ Client {client_id} connected. Total connections: {len(active_connections)}")
    
    heartbeat_task = None
    
    try:
        # Send initial connection confirmation with client_id
        await websocket.send_json({
            "type": "connection_ready",
            "client_id": client_id,
            "acp_version": ACP_API_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Connected to Odin backend (ACP v0.4.0)"
        })
        
        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(send_heartbeats(websocket, client_id))
        
        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Invalid JSON from client {client_id}")
                    continue
                
                message_type = payload.get("type")
                
                if message_type == "subscribe_request":
                    await handle_subscribe_request(websocket, client_id, payload)
                
                elif message_type == "unsubscribe_request":
                    await handle_unsubscribe_request(websocket, client_id, payload)
                
                elif message_type == "resync_request":
                    await handle_resync_request(websocket, client_id, payload)

                elif message_type == "client_sync":
                    await handle_client_sync_request(websocket, client_id, payload)
                
                else:
                    logger.debug(f"📨 Received {message_type} from client {client_id}")
                    
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"❌ Error in WebSocket connection {client_id}: {e}")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        
        # Clean up all sessions for this client
        deleted_sessions = session_manager.cleanup_client(client_id)

        # Ensure agent-side subscription state is cleaned for all deleted sessions.
        for session_id in deleted_sessions:
            for connection in agent_manager.connections.values():
                try:
                    if session_id in connection.subscriptions:
                        await connection.unsubscribe(session_id)
                except Exception as exc:
                    logger.warning(
                        f"Failed to unsubscribe session {session_id} on {connection.agent_id}: {exc}"
                    )
        
        # Clean up session data stores
        for session_id in deleted_sessions:
            if session_id in session_data_stores:
                del session_data_stores[session_id]
            session_seq_counters.pop(session_id, None)
            session_subscribe_epochs.pop(session_id, None)
            session_trade_revisions.pop(session_id, None)
            latest_trade_results_by_session.pop(session_id, None)
            applied_trade_strategy_by_session.pop(session_id, None)
            clear_history_response_state_for_session(session_id)
            for key in [k for k in indicator_seq_counters.keys() if k[1] == session_id]:
                indicator_seq_counters.pop(key, None)
        
        active_connections.pop(client_id, None)
        logger.info(f"❌ Client {client_id} disconnected. Cleaned up {len(deleted_sessions)} session(s). Total connections: {len(active_connections)}")


async def handle_subscribe_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict
) -> None:
    """Handle a subscription request from the frontend (ACP v0.4.0)"""
    logger.info(f"🔔 [handle_subscribe_request] Received from client {client_id}: {payload}")
    
    session_id = str(payload.get("session_id") or "").strip()
    agent_id = str(payload.get("agent_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    interval = str(payload.get("interval") or "").strip()
    timeframe_days = int(payload.get("timeframe_days") or 7)
    
    if not session_id or not agent_id or not symbol or not interval:
        await websocket.send_json({
            "type": "error",
            "code": "INVALID_REQUEST",
            "message": "session_id, agent_id, symbol, and interval are required",
        })
        logger.warning(f"Invalid subscribe_request from {client_id}: missing required fields")
        return

    request_epoch = int(session_subscribe_epochs.get(session_id, 0)) + 1
    session_subscribe_epochs[session_id] = request_epoch
    
    # Get agent connection
    connection = agent_manager.get_connection(agent_id)
    if not connection:
        await websocket.send_json({
            "type": "error",
            "agent_id": agent_id,
            "code": "AGENT_NOT_FOUND",
            "message": f"No active connection for agent {agent_id}",
        })
        logger.warning(f"Agent {agent_id} not found for client {client_id}")
        return
    
    # Use frontend-provided session_id (ACP v0.3.0: frontend owns session_id)
    # Create or retrieve session in SessionManager
    session = session_manager.create_session(
        session_id=session_id,
        client_id=client_id,
        agent_id=agent_id,
        symbol=symbol,
        interval=interval
    )
    session.agent_id = agent_id
    session.symbol = symbol
    session.interval = interval
    session.last_activity_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"📊 Using session {session_id} for client {client_id}: {agent_id} {symbol} @ {interval}")

    if not connection.metadata_fetched:
        metadata_valid = await connection.fetch_metadata()
        if not metadata_valid:
            await websocket.send_json({
                "type": "error",
                "code": "INVALID_REQUEST",
                "message": f"Failed to validate metadata for {agent_id}",
            })
            return

    if not connection.metadata or connection.metadata.get("agent_type") != "price":
        await websocket.send_json({
            "type": "error",
            "code": "UNSUPPORTED_OPERATION",
            "message": "Primary chart subscription must target a price agent",
        })
        return
    
    # Create SessionDataStore for this session
    data_store = SessionDataStore(
        session_id=session_id,
        agent_id=agent_id,
        symbol=symbol,
        interval=interval
    )
    data_store.update_retention(timeframe_days, interval)
    session_data_stores[session_id] = data_store
    session_seq_counters[session_id] = -1
    
    historical_bars = []

    # Fetch and ingest historical data if timeframe_days is specified
    if timeframe_days and timeframe_days > 0:
        now = datetime.now(timezone.utc)
        from_ts = connection._format_history_timestamp(now - timedelta(days=timeframe_days))
        to_ts = connection._format_history_timestamp(now)
        
        logger.info(f"📚 Fetching historical data for session {session_id}: {timeframe_days} days")
        historical_bars = await connection.fetch_history(symbol, from_ts, to_ts, interval)
        
        # Ingest bars into the session's data store
        ingested_count = 0
        for bar in historical_bars:
            result = data_store.ingest_ohlc(bar)
            if result:
                ingested_count += 1
        
        logger.info(f"💾 Ingested {ingested_count}/{len(historical_bars)} historical bars into session {session_id}")
        
    else:
        logger.info(
            f"📚 Skipping historical fetch for session {session_id}: timeframe_days={timeframe_days}"
        )

    if session_subscribe_epochs.get(session_id) != request_epoch:
        logger.info(
            "⏭️ Skipping stale subscribe flow for session %s (epoch=%s)",
            session_id,
            request_epoch,
        )
        return

    # Always send a snapshot so frontend can complete history-loading state.
    # Use canonical candles (all latest revisions) rather than finalized-only to include
    # in-flight bars like "session_reconciled" that haven't yet reached "final" state
    snapshot_bars = data_store.get_canonical_candles()
    snapshot_message = {
        "type": "snapshot",
        "session_id": session_id,
        "agent_id": agent_id,
        "symbol": symbol,
        "interval": interval,
        "bars": snapshot_bars,
        "count": len(snapshot_bars),
        "acp_version": ACP_API_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        logger.info(f"📸 [handle_subscribe_request] Sending snapshot to {client_id} with {snapshot_message['count']} bars...")
        await websocket.send_json(snapshot_message)
        logger.info(f"✅ [handle_subscribe_request] Snapshot sent successfully to {client_id}")
    except Exception as e:
        logger.error(f"❌ [handle_subscribe_request] Failed to send snapshot to {client_id}: {e}")
    
    # Subscribe to live data via AgentConnection
    logger.info(f"🔌 [handle_subscribe_request] Calling connection.subscribe for agent {agent_id}, session {session_id}: {symbol} @ {interval}")
    subscribe_result = await connection.subscribe(
        session_id=session_id,
        symbol=symbol,
        interval=interval,
        params={"timeframe_days": timeframe_days}
    )
    logger.info(f"✅ [handle_subscribe_request] connection.subscribe returned: {subscribe_result}")

    if session_subscribe_epochs.get(session_id) != request_epoch:
        logger.info(
            "⏭️ Skipping stale post-subscribe indicator flow for session %s (epoch=%s)",
            session_id,
            request_epoch,
        )
        return
    
    logger.info(f"📚 Price agent subscribed, now subscribing indicator agents for session {session_id}...")
    canonical_candles = data_store.get_canonical_candles()

    for indicator_agent in agent_manager.list_indicator_agents():
        indicator_config = indicator_agent.config
        if indicator_config.agent_id == agent_id:
            continue

        indicator_connection = agent_manager.get_connection(indicator_config.agent_id)
        if not indicator_connection:
            continue

        logger.info(f"🎯 Subscribing indicator agent {indicator_config.agent_id} for session {session_id}")
        indicator_params = sanitize_indicator_params(
            indicator_config.config_schema,
            indicator_config.indicators,
            indicator_config.selected_indicator_id,
        )

        indicator_subscribe_result = await indicator_connection.subscribe(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            params=indicator_params,
            force=True,
        )

        if indicator_subscribe_result and canonical_candles:
            if session_subscribe_epochs.get(session_id) != request_epoch:
                logger.info(
                    "⏭️ Skipping stale history_push for session %s (epoch=%s)",
                    session_id,
                    request_epoch,
                )
                return
            candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]
            await asyncio.sleep(SUBSCRIBE_PROPAGATION_DELAY_SECONDS)
            if session_subscribe_epochs.get(session_id) != request_epoch:
                logger.info(
                    "⏭️ Skipping stale delayed history_push for session %s (epoch=%s)",
                    session_id,
                    request_epoch,
                )
                return
            await indicator_connection.send_history_push(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                candles=candles_for_indicator,
            )
            logger.info(
                f"✅ Sent history_push with {len(candles_for_indicator)} candles to indicator agent {indicator_config.agent_id}"
            )


async def handle_unsubscribe_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict
) -> None:
    """Handle an unsubscribe request from the frontend"""
    session_id = str(payload.get("session_id") or "").strip()
    
    if not session_id:
        await websocket.send_json({
            "type": "error",
            "code": "INVALID_REQUEST",
            "message": "session_id is required",
        })
        return
    
    # Get session info
    session = session_manager.get_session(session_id)
    if not session:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_NOT_FOUND",
            "message": f"Session {session_id} not found",
        })
        return
    
    # Get agent connection and unsubscribe
    connection = agent_manager.get_connection(session.agent_id)
    if connection:
        await connection.unsubscribe(session_id)
    
    # Delete session
    session_manager.delete_session(session_id)
    
    # Clean up data store
    if session_id in session_data_stores:
        del session_data_stores[session_id]
    session_seq_counters.pop(session_id, None)
    session_subscribe_epochs.pop(session_id, None)
    session_trade_revisions.pop(session_id, None)
    latest_trade_results_by_session.pop(session_id, None)
    applied_trade_strategy_by_session.pop(session_id, None)
    clear_history_response_state_for_session(session_id)
    for key in [k for k in indicator_seq_counters.keys() if k[1] == session_id]:
        indicator_seq_counters.pop(key, None)
    
    logger.info(f"🚫 Unsubscribed session {session_id} for client {client_id}")


async def handle_resync_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict
) -> None:
    """Handle a resync request from the frontend after gap detection"""
    session_id = str(payload.get("session_id") or "").strip()
    last_seq_received = int(payload.get("last_seq_received") or -1)
    
    if not session_id or session_id not in session_data_stores:
        logger.warning(f"Resync request for unknown session {session_id}")
        return
    
    session = session_manager.get_session(session_id)
    if not session:
        return
    
    # Get messages from replay buffer since last_seq_received
    replay_buffer = session.replay_buffer
    messages = replay_buffer.get_messages_since(last_seq_received)
    
    # Send resync response
    resync_response = {
        "type": "resync_response",
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    try:
        await websocket.send_json(resync_response)
        logger.info(f"📤 Sent resync_response to {client_id} with {len(messages)} messages")
    except Exception as e:
        logger.error(f"Failed to send resync_response to {client_id}: {e}")


async def handle_client_sync_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict,
) -> None:
    """Handle revision-based client sync requests for stale-domain reconciliation."""
    incoming = payload.get("revisions")
    client_revisions = incoming if isinstance(incoming, dict) else {}

    stale_domains: list[str] = []
    server_revisions = _snapshot_domain_revisions()

    for domain, server_rev in server_revisions.items():
        try:
            client_rev = int(client_revisions.get(domain, -1))
        except (TypeError, ValueError):
            client_rev = -1
        if client_rev < server_rev:
            stale_domains.append(domain)

    trade_sessions: list[dict[str, Any]] = []
    if "trade" in stale_domains:
        for session in session_manager.get_client_sessions(client_id):
            result = latest_trade_results_by_session.get(session.session_id)
            if result:
                trade_sessions.append(
                    {
                        "session_id": session.session_id,
                        "result": result,
                    }
                )

    response = {
        "type": "sync_snapshot",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "server_revisions": server_revisions,
        "stale_domains": stale_domains,
        "trade_sessions": trade_sessions,
    }

    try:
        await websocket.send_json(response)
    except Exception as exc:
        logger.debug("Failed to send sync_snapshot to %s: %s", client_id, exc)


async def send_heartbeats(websocket: WebSocket, client_id: str):
    """Send periodic heartbeat messages to keep connection alive"""
    while True:
        try:
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
            await websocket.send_json({
                "type": "heartbeat",
                "acp_version": ACP_API_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.debug(f"💓 Heartbeat sent to {client_id}")
        except Exception as e:
            logger.debug(f"Heartbeat failed for {client_id}: {e}")
            break


async def route_agent_message(agent_id: str, session_id: str, message: dict) -> None:
    """
    Route ACP messages from agents to the appropriate session's WebSocket.
    
    ACP v0.4.0: Messages include session_id for routing.
    """
    message_type = message.get("type")
    
    # Find the session and its client
    session = session_manager.get_session(session_id)
    if not session:
        logger.warning(f"⚠️  Session {session_id} not found for {message_type} from {agent_id} - message dropped")
        return
    
    client_id = session.client_id
    websocket = active_connections.get(client_id)
    if not websocket:
        logger.warning(f"⚠️  Client {client_id} not connected for session {session_id} - message dropped")
        return
    
    # Process message based on type
    if message_type == "heartbeat":
        # Update agent status and forward
        agent = agent_manager.get_agent(agent_id)
        previous_status: str | None = None
        if agent:
            previous_status = agent.status.status
            agent.status.status = "online"
            agent.status.last_activity_ts = datetime.now(timezone.utc).isoformat()
            agent.status.error_message = None

        if previous_status and previous_status != "online":
            await emit_state_event(
                event_name="agent.status.changed",
                domain="agent",
                session_id=session_id,
                target_client_id=client_id,
                payload={
                    "agent_id": agent_id,
                    "status": "online",
                    "previous_status": previous_status,
                },
            )
        
        status_message = {
            "type": "heartbeat",
            "agent_id": agent_id,
            "session_id": session_id,
            "status": "online",
            "acp_version": ACP_API_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            await websocket.send_json(status_message)
            logger.debug(f"💓 Forwarded heartbeat to {client_id} for session {session_id}")
        except Exception as e:
            logger.debug(f"Failed to send heartbeat to {client_id}: {e}")
    
    elif message_type == "data" and message.get("schema") == "ohlc":
        record = message.get("record")
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_ohlc(record)

            if result:
                message["record"] = result
                # Suppress OHLC logs for noise reduction
                logger.debug(
                    f"📥 OHLC ingested for session {session_id}: id={result.get('id')} rev={result.get('rev')} state={result.get('bar_state')}"
                )

                for indicator_agent in agent_manager.list_indicator_agents():
                    indicator_connection = agent_manager.get_connection(indicator_agent.agent_id)
                    if not indicator_connection:
                        continue
                    if (
                        indicator_connection.metadata
                        and indicator_connection.metadata.get("agent_type") == "indicator"
                        and session_id in indicator_connection.subscriptions
                    ):
                        indicator_subscription = indicator_connection.subscriptions.get(session_id, {})
                        indicator_subscription_id = str(
                            indicator_subscription.get("subscription_id")
                            or f"{session_id}::{indicator_agent.agent_id}"
                        )
                        tick_message = {
                            "type": "tick_update",
                            "spec_version": ACP_SPEC_VERSION,
                            "session_id": session_id,
                            "subscription_id": indicator_subscription_id,
                            "agent_id": indicator_agent.agent_id,
                            "seq": next_indicator_seq(indicator_agent.agent_id, session_id),
                            "candle": to_indicator_ohlc(result),
                        }
                        await indicator_connection.send_message(tick_message)
                        logger.debug(f"📤 Sent tick_update to indicator agent {indicator_agent.agent_id}")
            else:
                logger.debug(f"⏭️  Duplicate OHLC skipped for session {session_id}")
                return

        # Ensure agent_id is set to the runtime instance ID
        message["agent_id"] = agent_id

        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
            logger.debug(f"📤 Forwarded OHLC data to {client_id} for session {session_id} (agent_id={agent_id})")
        except Exception as e:
            logger.debug(f"Failed to forward data to {client_id}: {e}")

    elif message_type == "candle_correction":
        record = message.get("record")
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_ohlc(record)

            if result:
                message["record"] = result
                logger.info(
                    f"🔄 Candle correction ingested for session {session_id}: id={result.get('id')} rev={result.get('rev')}"
                )

                for indicator_agent in agent_manager.list_indicator_agents():
                    indicator_connection = agent_manager.get_connection(indicator_agent.agent_id)
                    if not indicator_connection:
                        continue
                    if (
                        indicator_connection.metadata
                        and indicator_connection.metadata.get("agent_type") == "indicator"
                        and session_id in indicator_connection.subscriptions
                    ):
                        correction_message = {
                            "type": "candle_correction",
                            "spec_version": ACP_SPEC_VERSION,
                            "session_id": session_id,
                            "subscription_id": session_id,
                            "agent_id": indicator_agent.agent_id,
                            "seq": next_indicator_seq(indicator_agent.agent_id, session_id),
                            "candle": to_indicator_ohlc(result),
                            "reason": "Upstream correction",
                        }
                        await indicator_connection.send_message(correction_message)
                        logger.debug(
                            f"📤 Sent candle_correction to indicator agent {indicator_agent.agent_id}"
                        )
            else:
                logger.debug(f"⏭️  Candle correction skipped for session {session_id} (lower rev)")
                return

        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
        except Exception as e:
            logger.debug(f"Failed to forward candle_correction to {client_id}: {e}")
    
    elif message_type == "error":
        # Forward error message
        logger.error(f"❌ Agent {agent_id} error: {message.get('code')} - {message.get('message')}")
        error_code = str(message.get("code") or "")
        error_text = str(message.get("message") or "")

        recovered = False

        if error_code == "SUBSCRIPTION_NOT_FOUND":
            logger.warning(
                "[Recovery] Triggered by %s for indicator=%s session=%s message=%s",
                error_code,
                agent_id,
                session_id,
                error_text,
            )
            try:
                recovered = await recover_indicator_subscription(agent_id, session_id)
            except Exception as exc:
                logger.warning(
                    "[Recovery] Failed for indicator=%s session=%s: %s",
                    agent_id,
                    session_id,
                    exc,
                )

            if recovered:
                logger.info(
                    "[Recovery] Suppressing transient SUBSCRIPTION_NOT_FOUND for indicator=%s session=%s",
                    agent_id,
                    session_id,
                )
                agent = agent_manager.get_agent(agent_id)
                previous_status: str | None = None
                if agent:
                    previous_status = agent.status.status
                    agent.status.status = "online"
                    agent.status.error_message = None

                if previous_status == "error":
                    await emit_state_event(
                        event_name="agent.status.changed",
                        domain="agent",
                        session_id=session_id,
                        target_client_id=client_id,
                        payload={
                            "agent_id": agent_id,
                            "status": "online",
                            "previous_status": previous_status,
                            "error": None,
                        },
                    )
                return

        agent = agent_manager.get_agent(agent_id)
        previous_status: str | None = None
        if agent:
            previous_status = agent.status.status
            agent.status.status = "error"
            agent.status.error_message = str(message.get("message") or message.get("code") or "Unknown error")

        if previous_status != "error":
            await emit_state_event(
                event_name="agent.status.changed",
                domain="agent",
                session_id=session_id,
                target_client_id=client_id,
                payload={
                    "agent_id": agent_id,
                    "status": "error",
                    "previous_status": previous_status,
                    "error": message.get("message") or message.get("code"),
                },
            )
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to forward error to {client_id}: {e}")
    
    elif message_type == "history_response":
        # Overlay agent response to history_push (ACP-0.4.0 chunk-aware).
        subscription_id = str(message.get("subscription_id") or f"{session_id}::{agent_id}")
        overlays = message.get("overlays", [])
        if not isinstance(overlays, list):
            overlays = []

        chunk_index_raw = message.get("chunk_index")
        total_chunks_raw = message.get("total_chunks")
        is_final_chunk = bool(message.get("is_final_chunk"))
        merged_overlays = overlays

        if chunk_index_raw is not None:
            try:
                chunk_index = int(chunk_index_raw)
                total_chunks = int(total_chunks_raw)
            except (TypeError, ValueError):
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    "Chunk fields must be integers",
                )
                return

            chunk_key = (agent_id, session_id, subscription_id)
            now = datetime.now(timezone.utc)

            if chunk_index == 0:
                history_response_chunks[chunk_key] = {
                    "expected_chunk": 0,
                    "total_chunks": total_chunks,
                    "overlays": [],
                    "updated_at": now,
                }

            chunk_state = history_response_chunks.get(chunk_key)
            if not chunk_state:
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    f"Missing chunk state for chunk_index={chunk_index}",
                )
                return

            connection = agent_manager.get_connection(agent_id)
            timeout_seconds = DEFAULT_CHUNK_TIMEOUT_SECONDS
            if connection and connection.metadata:
                timeout_seconds = int(
                    connection.metadata.get("transport_limits", {}).get("chunk_timeout_seconds")
                    or DEFAULT_CHUNK_TIMEOUT_SECONDS
                )

            last_update: datetime = chunk_state["updated_at"]
            if (now - last_update).total_seconds() > timeout_seconds:
                history_response_chunks.pop(chunk_key, None)
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    f"Chunk timeout exceeded ({timeout_seconds}s)",
                )
                return

            expected_chunk = int(chunk_state["expected_chunk"])
            if chunk_index != expected_chunk:
                history_response_chunks.pop(chunk_key, None)
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    f"Expected chunk {expected_chunk}, received {chunk_index}",
                )
                return

            expected_total_chunks = int(chunk_state["total_chunks"])
            if total_chunks != expected_total_chunks:
                history_response_chunks.pop(chunk_key, None)
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    f"total_chunks changed from {expected_total_chunks} to {total_chunks}",
                )
                return

            chunk_state["overlays"].extend(overlays)
            chunk_state["expected_chunk"] = expected_chunk + 1
            chunk_state["updated_at"] = now

            if not is_final_chunk:
                return

            if int(chunk_state["expected_chunk"]) != expected_total_chunks:
                history_response_chunks.pop(chunk_key, None)
                await send_protocol_error_to_agent(
                    agent_id,
                    session_id,
                    subscription_id,
                    "CHUNK_SEQUENCE_ERROR",
                    "Final chunk received before all chunks were delivered",
                )
                return

            merged_overlays = list(chunk_state["overlays"])
            history_response_chunks.pop(chunk_key, None)
            message["chunk_index"] = 0
            message["total_chunks"] = 1
            message["is_final_chunk"] = True
            message["count"] = len(merged_overlays)

        logger.info(
            "📚 Received history_response from %s for session %s with %s overlays",
            agent_id,
            session_id,
            len(merged_overlays),
        )
        
        # Ingest overlay records into session data store
        if session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            ingested_count = 0
            for overlay_record in merged_overlays:
                # Non-OHLC records use simple id-based dedup
                result = data_store.ingest_non_ohlc(
                    overlay_record,
                    source_agent_id=agent_id,
                    schema=str(overlay_record.get("schema") or message.get("schema") or ""),
                    subscription_id=str(
                        overlay_record.get("subscription_id")
                        or message.get("subscription_id")
                        or f"{session_id}::{agent_id}"
                    ),
                    output_id=str(overlay_record.get("output_id") or message.get("output_id") or ""),
                )
                if result:
                    ingested_count += 1
                    logger.debug(f"📥 Overlay record ingested: id={result.get('id')}")
            if ingested_count > 0:
                _record_overlay_ingest(ingested_count)
            _increment_telemetry_counter("overlay_ingest_history_messages_total")
        
        # Ensure agent_id is set to the runtime instance ID
        message["agent_id"] = agent_id
        message["overlays"] = merged_overlays
        
        # Forward to frontend
        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
            logger.info(f"📤 Forwarded history_response with {len(merged_overlays)} overlays to {client_id}")
        except Exception as e:
            logger.debug(f"Failed to forward history_response to {client_id}: {e}")

        await emit_state_event(
            event_name="overlay.history.updated",
            domain="overlay",
            session_id=session_id,
            target_client_id=client_id,
            payload={
                "session_id": session_id,
                "agent_id": agent_id,
                "count": len(merged_overlays),
            },
        )
    
    elif message_type == "overlay_update":
        # Live overlay value update from overlay agent (suppress logs for noise reduction)
        original_agent_id = message.get("agent_id", "not_set")
        record = message.get("record")
        schema = message.get("schema")
        
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_non_ohlc(
                record,
                source_agent_id=agent_id,
                schema=str(message.get("schema") or record.get("schema") or ""),
                subscription_id=str(message.get("subscription_id") or f"{session_id}::{agent_id}"),
                output_id=str(message.get("output_id") or record.get("output_id") or ""),
            )
            
            if result:
                _record_overlay_ingest(1)
                _increment_telemetry_counter("overlay_ingest_update_messages_total")
                message["record"] = result
            else:
                return
        
        # Ensure agent_id is set to the runtime instance ID
        message["agent_id"] = agent_id
        
        # Forward to frontend
        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
        except Exception as e:
            logger.debug(f"Failed to forward overlay_update to {client_id}: {e}")
    
    elif message_type == "overlay_marker":
        # Event marker from overlay agent
        record = message.get("record")
        
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_non_ohlc(
                record,
                source_agent_id=agent_id,
                schema=str(message.get("schema") or record.get("schema") or "event"),
                subscription_id=str(message.get("subscription_id") or f"{session_id}::{agent_id}"),
                output_id=str(message.get("output_id") or record.get("output_id") or ""),
            )
            
            if result:
                message["record"] = result
                logger.info(f"📍 Overlay marker ingested for session {session_id}: id={result.get('id')}")
            else:
                logger.debug(f"⏭️  Duplicate overlay marker skipped for session {session_id}")
                return
        
        # Forward to frontend
        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
            logger.debug(f"📤 Forwarded overlay_marker to {client_id} for session {session_id}")
        except Exception as e:
            logger.debug(f"Failed to forward overlay_marker to {client_id}: {e}")
    
    else:
        # Forward other message types (tick_update, history_push, etc.)
        try:
            replay_message = buffer_replay_message(session, message)
            await websocket.send_json(replay_message)
            logger.debug(f"📤 Forwarded {message_type} to {client_id} for session {session_id}")
        except Exception as e:
            logger.debug(f"Failed to forward {message_type} to {client_id}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
