"""
Agent management service

Handles loading agent configurations from YAML and managing agent lifecycle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.models import Agent, AgentConfig, AgentStatus

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages agent configurations and lifecycle"""
    
    def __init__(self):
        self.agents: dict[str, Agent] = {}
    
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


# Global agent manager instance
agent_manager = AgentManager()
