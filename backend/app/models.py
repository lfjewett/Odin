"""
Data models for Odin backend
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
            "config": self.config.config_schema,
            "spec_version": self.config.spec_version,
            "agent_version": self.config.agent_version,
            "description": self.config.description,
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