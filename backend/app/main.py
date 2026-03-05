"""
Odin Backend - FastAPI WebSocket Server

This is the MVP backend that acts as a traffic cop:
- Accepts WebSocket connections from the frontend
- Loads agent configurations from overlay_agents.yaml
- Provides REST API for agent management
- Will eventually subscribe to ACP agents and merge their streams
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Set

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent_manager import agent_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Track active WebSocket connections
active_connections: Set[WebSocket] = set()

# Background tasks
monitor_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle"""
    global monitor_task
    
    logger.info("🚀 Odin backend starting...")
    
    # Load agent configurations from YAML
    config_path = Path(__file__).parent.parent.parent / "overlay_agents.yaml"
    logger.info(f"📂 Loading agent configs from: {config_path}")
    agent_manager.load_from_yaml(config_path)
    
    # Start agent health monitor
    monitor_task = asyncio.create_task(monitor_agent_health())
    logger.info("🏥 Agent health monitor started")
    
    logger.info("📡 WebSocket endpoint available at: ws://localhost:8001/ws")
    yield
    
    # Cleanup
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    logger.info("🛑 Odin backend shutting down...")


app = FastAPI(
    title="Odin Backend",
    description="Trading platform backend - WebSocket stream router",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8000", "http://127.0.0.1:8000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "odin-backend",
        "version": "0.1.0",
        "status": "running",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(active_connections),
        "agents_loaded": len(agent_manager.list_agents()),
        "timestamp": datetime.now(UTC).isoformat(),
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for frontend connections
    
    Protocol (MVP):
    - Client connects
    - Server accepts connection (proves backend is alive)
    - Server sends periodic heartbeat messages
    - Future: Server will stream merged ACP events from agents
    """
    await websocket.accept()
    active_connections.add(websocket)
    client_id = id(websocket)
    logger.info(f"✅ Client {client_id} connected. Total connections: {len(active_connections)}")
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": "Connected to Odin backend"
        })
        
        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(send_heartbeats(websocket))
        
        # Listen for client messages (currently we don't expect any)
        while True:
            try:
                # This will raise WebSocketDisconnect if client disconnects
                data = await websocket.receive_text()
                logger.debug(f"📨 Received from client {client_id}: {data}")
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"❌ Error in WebSocket connection {client_id}: {e}")
    finally:
        heartbeat_task.cancel()
        active_connections.discard(websocket)
        logger.info(f"❌ Client {client_id} disconnected. Total connections: {len(active_connections)}")


async def send_heartbeats(websocket: WebSocket):
    """Send periodic heartbeat messages to keep connection alive"""
    while True:
        try:
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
            await websocket.send_json({
                "type": "heartbeat",
                "timestamp": datetime.now(UTC).isoformat(),
            })
            logger.debug(f"💓 Heartbeat sent to client {id(websocket)}")
        except Exception as e:
            logger.debug(f"Heartbeat failed for client {id(websocket)}: {e}")
            break


async def check_agent_health(agent_id: str, agent_url: str) -> tuple[str, str | None]:
    """
    Check if an agent is healthy by making an HTTP request
    
    Returns: (status, error_message)
    """
    try:
        timeout = aiohttp.ClientTimeout(total=3)  # 3 second timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{agent_url}/health") as response:
                if response.status == 200:
                    return ("online", None)
                else:
                    return ("offline", f"HTTP {response.status}")
    except asyncio.TimeoutError:
        return ("offline", "Connection timeout")
    except Exception as e:
        return ("offline", str(e))


async def monitor_agent_health():
    """
    Background task that periodically checks agent health
    and broadcasts status updates to connected clients
    """
    logger.info("🏥 Starting agent health monitor loop")
    
    while True:
        try:
            await asyncio.sleep(10)  # Check every 10 seconds
            
            # Check each agent's health
            for agent in agent_manager.list_agents():
                status, error = await check_agent_health(agent.config.agent_id, agent.config.agent_url)
                old_status = agent.status.status
                
                # Update agent status if it changed
                if old_status != status:
                    agent.status.status = status
                    agent.status.last_activity_ts = datetime.now(UTC).isoformat()
                    agent.status.error_message = error
                    agent.updated_at = datetime.now(UTC).isoformat()
                    
                    logger.info(f"⚠️  Agent {agent.agent_id} status changed: {old_status} → {status}")
                    
                    # Broadcast status update to all connected clients
                    if active_connections:
                        update_message = {
                            "type": "agent_status_update",
                            "agent_id": agent.config.agent_id,
                            "status": status,
                            "error_message": error,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                        
                        disconnected = set()
                        for websocket in active_connections:
                            try:
                                await websocket.send_json(update_message)
                            except Exception as e:
                                logger.debug(f"Failed to send status update: {e}")
                                disconnected.add(websocket)
                        
                        # Clean up disconnected clients
                        for ws in disconnected:
                            active_connections.discard(ws)
                
        except asyncio.CancelledError:
            logger.info("🏥 Agent health monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in health monitor: {e}")
            await asyncio.sleep(5)  # Back off on error


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
