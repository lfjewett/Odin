"""
Data models for Odin backend
"""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Keys that are managed purely by the UI and must survive the sanitize/normalize
# pipeline without being declared in the agent's params_schema.
# IMPORTANT: If you add a new UI-only config key here you must ALSO update the
# POST /api/agents and PATCH /api/agents/{id} handlers in main.py so the key
# is forwarded into the stored config (search for "_ui_keys" in main.py).
UI_MANAGED_INDICATOR_CONFIG_KEYS = {
    "line_color",
    "vwap_line_color",
    "vwap_upper_band_color",
    "vwap_lower_band_color",
    "vwap_line_style",
    "vwap_upper_band_style",
    "vwap_lower_band_style",
    "visible",
    "aggregation_interval",
    "force_subgraph",
    "area_use_source_style",
    "area_show_labels",
    "area_fill_mode",
    "area_fill_opacity",
    "area_conditional_up_color",
    "area_conditional_down_color",
}


def _normalize_indicator_instance_agent_id(agent_id: str) -> str:
    return re.sub(r"__\d+$", "", agent_id or "")


def get_selected_indicator_definition(
    agent_id: str,
    indicators: list[dict[str, Any]] | None,
    outputs: list[dict[str, Any]] | None = None,
    config_schema: dict[str, Any] | None = None,
    selected_indicator_id: str | None = None,
) -> dict[str, Any] | None:
    catalog = indicators or []
    if not catalog:
        return None

    if selected_indicator_id:
        matched = next(
            (item for item in catalog if item.get("indicator_id") == selected_indicator_id),
            None,
        )
        if matched:
            return matched

    normalized_agent_id = _normalize_indicator_instance_agent_id(agent_id)
    matched_by_agent_id = next(
        (
            item
            for item in catalog
            if normalized_agent_id.endswith(f"__{item.get('indicator_id', '')}")
        ),
        None,
    )
    if matched_by_agent_id:
        return matched_by_agent_id

    if outputs:
        matched_by_outputs = next(
            (item for item in catalog if (item.get("outputs") or []) == outputs),
            None,
        )
        if matched_by_outputs:
            return matched_by_outputs

    configured_keys = {
        key for key in (config_schema or {}).keys() if key not in UI_MANAGED_INDICATOR_CONFIG_KEYS
    }
    if configured_keys:
        best_match: tuple[int, dict[str, Any]] | None = None
        for item in catalog:
            params_schema = item.get("params_schema") or {}
            if not isinstance(params_schema, dict):
                continue

            schema_keys = {key for key in params_schema.keys() if key not in UI_MANAGED_INDICATOR_CONFIG_KEYS}
            if not schema_keys:
                continue

            overlap = len(schema_keys & configured_keys)
            if overlap == 0:
                continue

            exact_match = schema_keys == configured_keys
            score = overlap * 10 + (100 if exact_match else 0) - abs(len(schema_keys) - len(configured_keys))
            if best_match is None or score > best_match[0]:
                best_match = (score, item)

        if best_match:
            return best_match[1]

    if len(catalog) == 1:
        return catalog[0]

    return None


def infer_selected_indicator_id(
    agent_id: str,
    indicators: list[dict[str, Any]] | None,
    outputs: list[dict[str, Any]] | None = None,
    config_schema: dict[str, Any] | None = None,
    selected_indicator_id: str | None = None,
) -> str | None:
    selected = get_selected_indicator_definition(
        agent_id=agent_id,
        indicators=indicators,
        outputs=outputs,
        config_schema=config_schema,
        selected_indicator_id=selected_indicator_id,
    )
    indicator_id = selected.get("indicator_id") if selected else None
    return str(indicator_id) if indicator_id else None


def normalize_indicator_config(
    agent_id: str,
    config_schema: dict[str, Any] | None,
    indicators: list[dict[str, Any]] | None,
    outputs: list[dict[str, Any]] | None = None,
    selected_indicator_id: str | None = None,
) -> dict[str, Any]:
    config = dict(config_schema or {})
    selected = get_selected_indicator_definition(
        agent_id=agent_id,
        indicators=indicators,
        outputs=outputs,
        config_schema=config,
        selected_indicator_id=selected_indicator_id,
    )
    if not selected:
        return config

    params_schema = selected.get("params_schema") or {}
    if not isinstance(params_schema, dict):
        return config

    allowed_keys = set(params_schema.keys()) | UI_MANAGED_INDICATOR_CONFIG_KEYS
    return {key: value for key, value in config.items() if key in allowed_keys}


def normalize_indicator_outputs_and_description(
    outputs: list[dict[str, Any]] | None,
    description: str,
    indicators: list[dict[str, Any]] | None,
    agent_id: str,
    config_schema: dict[str, Any] | None,
    selected_indicator_id: str | None = None,
) -> tuple[list[dict[str, Any]], str, str | None]:
    selected = get_selected_indicator_definition(
        agent_id=agent_id,
        indicators=indicators,
        outputs=outputs,
        config_schema=config_schema,
        selected_indicator_id=selected_indicator_id,
    )
    if not selected:
        return list(outputs or []), description, None

    normalized_outputs = list(selected.get("outputs") or outputs or [])
    normalized_description = str(selected.get("description") or description or "")
    indicator_id = selected.get("indicator_id")
    return normalized_outputs, normalized_description, str(indicator_id) if indicator_id else None


class AgentConfig(BaseModel):
    """Configuration schema for an agent"""
    spec_version: str = Field(description="ACP protocol version")
    agent_url: str = Field(description="Base URL for agent HTTP/WS endpoints")
    agent_id: str = Field(description="Unique identifier for agent")
    agent_name: str = Field(description="Human-readable agent name")
    agent_version: str = Field(description="Agent version string")
    description: str = Field(description="Agent description")
    agent_type: Literal["price", "indicator", "event"] = Field(description="Agent role")
    config_schema: dict[str, Any] = Field(default_factory=dict, description="Agent-specific configuration")
    outputs: list[dict[str, Any]] = Field(default_factory=list, description="Typed output descriptors")
    indicators: list[dict[str, Any]] = Field(default_factory=list, description="Discoverable indicator catalog")
    selected_indicator_id: str | None = Field(default=None, description="Selected indicator from indicators catalog")
    transport_limits: dict[str, Any] = Field(default_factory=dict, description="ACP transport limits")


class AgentStatus(BaseModel):
    """Runtime status of an agent"""
    agent_id: str
    status: Literal["online", "offline", "error", "connecting"] = "offline"
    last_activity_ts: str | None = None
    error_message: str | None = None
    uptime_seconds: float = 0


class Agent(BaseModel):
    """Complete agent representation with config and status"""
    config: AgentConfig
    status: AgentStatus
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    @property
    def agent_id(self) -> str:
        return self.config.agent_id
    
    @property
    def agent_name(self) -> str:
        return self.config.agent_name
    
    def to_frontend_format(self) -> dict[str, Any]:
        """Convert to format expected by frontend"""
        normalized_config = self.config.config_schema
        selected_indicator_id = self.config.selected_indicator_id
        if self.config.agent_type == "indicator":
            normalized_config = normalize_indicator_config(
                agent_id=self.config.agent_id,
                config_schema=self.config.config_schema,
                indicators=self.config.indicators,
                outputs=self.config.outputs,
                selected_indicator_id=self.config.selected_indicator_id,
            )
            selected_indicator_id = infer_selected_indicator_id(
                agent_id=self.config.agent_id,
                indicators=self.config.indicators,
                outputs=self.config.outputs,
                config_schema=normalized_config,
                selected_indicator_id=self.config.selected_indicator_id,
            )

        return {
            "id": self.config.agent_id,
            "name": self.config.agent_name,
            "agent_type": self.config.agent_type,
            "agent_url": self.config.agent_url,
            "output_schema": self.config.outputs[0]["schema"] if self.config.outputs else None,
            "outputs": self.config.outputs,
            "indicators": self.config.indicators,
            "enabled": True,
            "status": self.status.status,
            "last_activity_ts": self.status.last_activity_ts,
            "error_message": self.status.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "config": normalized_config,
            "spec_version": self.config.spec_version,
            "agent_version": self.config.agent_version,
            "description": self.config.description,
            "selected_indicator_id": selected_indicator_id,
            "transport_limits": self.config.transport_limits,
        }

# ============================================================================
# ACP v0.3.0 Session & Sequence Management
# ============================================================================


class SequenceTracker:
    """Tracks sequence numbers and detects gaps for a session"""
    
    def __init__(self):
        self.last_seq_received: int | None = None
    
    def update(self, seq: int) -> tuple[bool, int | None]:
        """
        Update with new sequence number. Returns (has_gap, gap_start).
        
        Returns:
            (has_gap, gap_start):
                - has_gap: True if gap detected
                - gap_start: First missing seq number (None if no gap)
        """
        if self.last_seq_received is None:
            self.last_seq_received = seq
            return (False, None)
        
        expected = self.last_seq_received + 1
        if seq < expected:
            # Duplicate or out-of-order, ignore
            return (False, None)
        elif seq == expected:
            # Normal progression
            self.last_seq_received = seq
            return (False, None)
        else:
            # Gap detected
            gap_start = expected
            self.last_seq_received = seq
            return (True, gap_start)


class ReplayBuffer:
    """Fixed-size buffer for message replay on resync"""
    
    def __init__(self, max_size: int = 100):
        self.buffer: deque[dict[str, Any]] = deque(maxlen=max_size)
    
    def append(self, message: dict[str, Any]) -> None:
        """Add message to buffer"""
        self.buffer.append(message)
    
    def get_messages_since(self, seq: int) -> list[dict[str, Any]]:
        """Get all messages with sequence number > seq"""
        return [msg for msg in self.buffer if msg.get("seq", 0) > seq]
    
    def clear(self) -> None:
        """Clear the buffer"""
        self.buffer.clear()


class Session(BaseModel):
    """ACP v0.3.0 Session: isolated chart view for a client"""
    
    session_id: str  # Now required, no default
    client_id: str
    agent_id: str
    symbol: str
    interval: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_activity_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # Runtime trackers (not serialized)
    _sequence_tracker: SequenceTracker | None = None
    _replay_buffer: ReplayBuffer | None = None
    
    def __init__(self, **data):
        super().__init__(**data)
        self._sequence_tracker = SequenceTracker()
        self._replay_buffer = ReplayBuffer(max_size=100)
    
    @property
    def sequence_tracker(self) -> SequenceTracker:
        if self._sequence_tracker is None:
            self._sequence_tracker = SequenceTracker()
        return self._sequence_tracker
    
    @property
    def replay_buffer(self) -> ReplayBuffer:
        if self._replay_buffer is None:
            self._replay_buffer = ReplayBuffer(max_size=100)
        return self._replay_buffer
    
    def update_activity(self) -> None:
        """Update last activity timestamp"""
        self.last_activity_at = datetime.now(timezone.utc).isoformat()


class Variable(BaseModel):
    """Represents a data variable available in a trading session"""
    name: str = Field(description="Display name for the variable (e.g., 'OPEN', 'SMA-20:value')")
    type: Literal["ohlcv", "indicator"] = Field(description="Variable type")
    schema: str = Field(description="Data schema (e.g., 'number', 'line', 'band', 'histogram')")
    agent_id: str | None = Field(default=None, description="Source agent ID for indicators")
    output_id: str | None = Field(default=None, description="Output ID from agent metadata")


def build_variable_name(agent_name: str, output: dict[str, Any]) -> str:
    """
    Construct variable name from agent name and output descriptor.
    
    Format: {agent-name}:{output-label} or {agent-name}:{output-id}
    For multi-output indicators, field names are appended: {agent-name}:{label}:field
    """
    label = output.get("label", output.get("output_id", "value"))
    return f"{agent_name}:{label}"


def _build_area_canonical_base_name(agent_name: str, output: dict[str, Any]) -> str:
    """Build canonical base name for area-derived variables, collapsing duplicate label prefixes."""
    base_name = build_variable_name(agent_name, output)
    output_label = str(output.get("label", output.get("output_id", "value"))).strip()
    normalized_agent_name = str(agent_name).strip()
    if normalized_agent_name and output_label == normalized_agent_name:
        return normalized_agent_name
    return base_name


def build_area_field_variable_name(agent_name: str, output: dict[str, Any], field: str) -> str:
    """Construct the canonical variable name for an area numeric field (upper/lower)."""
    base_name = _build_area_canonical_base_name(agent_name, output)
    normalized_field = str(field).strip().lower()
    return f"{base_name}:{normalized_field}"


def build_area_field_legacy_variable_name(agent_name: str, output: dict[str, Any], field: str) -> str:
    """Construct the legacy variable name for an area numeric field (upper/lower)."""
    base_name = build_variable_name(agent_name, output)
    normalized_field = str(field).strip().lower()
    return f"{base_name}:{normalized_field}"


def build_area_metadata_variable_name(agent_name: str, output: dict[str, Any], metadata_key: str) -> str:
    """Construct the canonical DSL-safe variable name for an area metadata numeric field."""
    base_name = build_variable_name(agent_name, output)
    normalized_key = re.sub(r"[^A-Za-z0-9_:\-]", "_", str(metadata_key).strip())
    normalized_key = re.sub(r"_+", "_", normalized_key).strip("_:")
    if not normalized_key:
        normalized_key = "field"
    if normalized_key[0].isdigit():
        normalized_key = f"f_{normalized_key}"

    # For metadata fields, avoid duplicate prefixes when output label == agent name.
    # Example: Gungnir:Gungnir:meta_dist -> canonical Gungnir:dist
    base_name = _build_area_canonical_base_name(agent_name, output)

    return f"{base_name}:{normalized_key}"


def build_area_metadata_legacy_variable_name(agent_name: str, output: dict[str, Any], metadata_key: str) -> str:
    """Construct the legacy DSL-safe variable name for an area metadata numeric field."""
    base_name = build_variable_name(agent_name, output)
    normalized_key = re.sub(r"[^A-Za-z0-9_:\-]", "_", str(metadata_key).strip())
    normalized_key = re.sub(r"_+", "_", normalized_key).strip("_:")
    if not normalized_key:
        normalized_key = "field"
    if normalized_key[0].isdigit():
        normalized_key = f"f_{normalized_key}"
    return f"{base_name}:meta_{normalized_key}"


class SessionManager:
    """Manages session lifecycle"""
    
    def __init__(self):
        self._sessions: dict[str, Session] = {}  # session_id -> Session
        self._client_sessions: dict[str, list[str]] = {}  # client_id -> [session_ids]
    
    def create_session(self, session_id: str, client_id: str, agent_id: str, symbol: str, interval: str) -> Session:
        """Create a new session with frontend-provided session_id"""
        # Check if session already exists
        if session_id in self._sessions:
            logger.info(f"Session {session_id} already exists, returning existing session")
            return self._sessions[session_id]
        
        session = Session(
            session_id=session_id,
            client_id=client_id,
            agent_id=agent_id,
            symbol=symbol,
            interval=interval
        )
        self._sessions[session.session_id] = session
        
        if client_id not in self._client_sessions:
            self._client_sessions[client_id] = []
        self._client_sessions[client_id].append(session.session_id)
        
        return session
    
    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID"""
        return self._sessions.get(session_id)
    
    def get_client_sessions(self, client_id: str) -> list[Session]:
        """Retrieve all sessions for a client"""
        session_ids = self._client_sessions.get(client_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and clean up client index"""
        if session_id not in self._sessions:
            return False
        
        session = self._sessions.pop(session_id)
        
        if session.client_id in self._client_sessions:
            self._client_sessions[session.client_id].remove(session_id)
            if not self._client_sessions[session.client_id]:
                del self._client_sessions[session.client_id]
        
        return True
    
    def cleanup_client(self, client_id: str) -> list[str]:
        """Delete all sessions for a client and return deleted session IDs"""
        session_ids = self._client_sessions.pop(client_id, [])
        deleted = []
        
        for session_id in session_ids:
            if session_id in self._sessions:
                del self._sessions[session_id]
                deleted.append(session_id)
        
        return deleted
    
    def list_all_sessions(self) -> list[Session]:
        """List all active sessions"""
        return list(self._sessions.values())