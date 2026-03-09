"""
Agent management service

Handles loading agent configurations from YAML and managing agent lifecycle.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Callable
from datetime import datetime, timezone

import yaml

from app.models import Agent, AgentConfig, AgentStatus

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages agent configurations and lifecycle"""
    
    def __init__(self):
        self.agents: dict[str, Agent] = {}
        self.connections: dict[str, Any] = {}  # agent_id -> AgentConnection
        self.on_agent_message: Callable[[str, dict[str, Any]], Any] | None = None
        self.config_file_path: Path | None = None
    
    def load_from_yaml(self, yaml_path: str | Path) -> None:
        """Load agent configurations from YAML file"""
        yaml_path = Path(yaml_path)
        
        if not yaml_path.exists():
            logger.warning(f"Agent config file not found: {yaml_path}")
            return
        
        try:
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            
            if not data or "agents" not in data:
                logger.warning(f"No agents found in {yaml_path}")
                return
            
            agent_list = data["agents"]
            if not isinstance(agent_list, list):
                logger.error(f"'agents' in {yaml_path} must be a list")
                return
            
            loaded_count = 0
            for agent_data in agent_list:
                try:
                    agent_config = AgentConfig(**agent_data)
                    agent_status = AgentStatus(agent_id=agent_config.agent_id)
                    agent = Agent(config=agent_config, status=agent_status)
                    
                    self.agents[agent.agent_id] = agent
                    loaded_count += 1
                    logger.info(f"✅ Loaded agent: {agent.agent_name} ({agent.agent_id})")
                    
                except Exception as e:
                    logger.error(f"Failed to load agent from config: {e}")
                    continue
            
            logger.info(f"📋 Loaded {loaded_count} agent(s) from {yaml_path}")
            
        except Exception as e:
            logger.error(f"Failed to load agent config from {yaml_path}: {e}")
    
    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an agent by ID"""
        return self.agents.get(agent_id)
    
    def list_agents(self) -> list[Agent]:
        """Get all configured agents"""
        return list(self.agents.values())
    
    def get_agent_for_frontend(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent in frontend-compatible format"""
        agent = self.get_agent(agent_id)
        return agent.to_frontend_format() if agent else None
    
    def list_agents_for_frontend(self) -> list[dict[str, Any]]:
        """Get all agents in frontend-compatible format"""
        return [agent.to_frontend_format() for agent in self.agents.values()]
    
    def get_connection(self, agent_id: str) -> Any | None:
        """Get agent connection by ID"""
        return self.connections.get(agent_id)

    def add_or_update_agent(self, agent_config: AgentConfig) -> Agent:
        """Register or update an agent at runtime."""
        existing = self.agents.get(agent_config.agent_id)
        if existing:
            existing.config = agent_config
            existing.updated_at = datetime.now(timezone.utc).isoformat()
            return existing

        agent_status = AgentStatus(agent_id=agent_config.agent_id)
        agent = Agent(config=agent_config, status=agent_status)
        self.agents[agent.agent_id] = agent
        logger.info(f"✅ Registered runtime agent: {agent.agent_name} ({agent.agent_id})")
        return agent

    def list_indicator_agents(self) -> list[Agent]:
        """Get all indicator agents."""
        return [agent for agent in self.agents.values() if agent.config.agent_type == "indicator"]
    
    async def send_cached_snapshots_to_client(self, callback: Callable) -> None:
        """Send all cached snapshots to a newly connected client"""
        for agent_id, connection in self.connections.items():
            if hasattr(connection, 'cached_snapshot') and connection.cached_snapshot:
                logger.info(f"📸 Sending cached snapshot from {agent_id} to new client")
                if inspect.iscoroutinefunction(callback):
                    await callback(agent_id, connection.cached_snapshot)
                else:
                    callback(agent_id, connection.cached_snapshot)
    
    def add_connection(self, agent_id: str, connection: Any) -> None:
        """Register an agent connection"""
        self.connections[agent_id] = connection
        logger.debug(f"Registered connection for agent: {agent_id}")
    
    def remove_connection(self, agent_id: str) -> None:
        """Remove an agent connection"""
        if agent_id in self.connections:
            del self.connections[agent_id]
            logger.debug(f"Removed connection for agent: {agent_id}")
    
    async def start_all_connections(self) -> None:
        """Start WebSocket connections to all configured agents (ACP v0.3.0)."""
        from app.agent_connection import AgentConnection
        
        logger.info(f"🔌 Starting connections for {len(self.agents)} agents...")
        
        for agent in self.agents.values():
            # ACP v0.3.0: Connect to all agents (price, indicator, event, etc.)
            logger.info(f"📡 Starting connection to {agent.agent_id} at {agent.config.agent_url}...")
            
            connection = AgentConnection(
                agent=agent,
                on_message=self.on_agent_message
            )
            
            self.add_connection(agent.agent_id, connection)
            
            logger.info("   Attempting to connect...")
            success = await connection.start()
            if success:
                logger.info(f"✅ Successfully connected to {agent.agent_id}")
            else:
                logger.error(f"❌ Failed to connect to {agent.agent_id}")
        
        logger.info(f"📊 Connection summary: {len(self.connections)} connections established")
    
    async def stop_all_connections(self) -> None:
        """Stop all agent connections"""
        for agent_id, connection in list(self.connections.items()):
            logger.info(f"Stopping connection to {agent_id}...")
            await connection.stop()
            self.remove_connection(agent_id)
    
    def persist_agents_to_yaml(self, yaml_path: str | Path) -> None:
        """
        Persist all current agents to YAML file.
        
        Converts Agent objects to their YAML representation and writes to file.
        """
        yaml_path = Path(yaml_path)
        try:
            agents_data = []
            for agent in self.agents.values():
                agent_dict = {
                    "spec_version": agent.config.spec_version,
                    "agent_url": agent.config.agent_url,
                    "agent_id": agent.config.agent_id,
                    "agent_name": agent.config.agent_name,
                    "agent_version": agent.config.agent_version,
                    "description": agent.config.description,
                    "agent_type": agent.config.agent_type,
                    "config_schema": agent.config.config_schema,
                    "outputs": agent.config.outputs,
                }
                if agent.config.indicators:
                    agent_dict["indicators"] = agent.config.indicators
                agents_data.append(agent_dict)
            
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            with open(yaml_path, "w") as f:
                yaml.dump({"agents": agents_data}, f, default_flow_style=False, sort_keys=False)
            
            logger.info(f"💾 Persisted {len(agents_data)} agent(s) to {yaml_path}")
        except Exception as e:
            logger.error(f"Failed to persist agents to {yaml_path}: {e}")


# Global agent manager instance
agent_manager = AgentManager()
