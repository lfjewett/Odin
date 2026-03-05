"""
Data models for Odin backend
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration schema for an agent"""
    spec_version: str = Field(description="ACP protocol version")
    agent_url: str = Field(description="Base URL for agent HTTP/WS endpoints")
    agent_id: str = Field(description="Unique identifier for agent")
    agent_name: str = Field(description="Human-readable agent name")
    agent_version: str = Field(description="Agent version string")
    description: str = Field(description="Agent description")
    config_schema: dict[str, Any] = Field(default_factory=dict, description="Agent-specific configuration")
    output_schema: str = Field(description="Output schema type (ohlc, line, event, etc)")


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
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    
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
            "agent_type": self.config.agent_id,
            "agent_url": self.config.agent_url,
            "output_schema": self.config.output_schema,
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
