"""
Odin Backend - FastAPI WebSocket Server

Subscribes to ACP agents and routes their streams to the frontend:
- Accepts WebSocket connections from the frontend
- Loads agent configurations from overlay_agents.yaml
- Connects to ACP agents via WebSocket
- Forwards ACP data/heartbeat/error messages to frontend
- Provides REST API for agent management
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Set

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle"""
    logger.info("🚀 Odin backend starting...")
    
    # Load agent configurations from YAML
    config_path = Path(__file__).parent.parent.parent / "overlay_agents.yaml"
    logger.info(f"📂 Loading agent configs from: {config_path}")
    agent_manager.load_from_yaml(config_path)
    
    # Set up message callback to forward ACP messages to frontend
    agent_manager.on_agent_message = broadcast_agent_message
    
    # Start WebSocket connections to all agents (no market subscription yet)
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(active_connections),
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
    
    heartbeat_task = None  # Initialize to avoid UnboundLocalError
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Connected to Odin backend"
        })
        
        # Send cached snapshots to new client
        async def send_to_client(agent_id: str, message: dict) -> None:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send cached snapshot to client {client_id}: {e}")
        
        await agent_manager.send_cached_snapshots_to_client(send_to_client)
        
        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(send_heartbeats(websocket))
        
        # Listen for client messages (currently we don't expect any)
        while True:
            try:
                # This will raise WebSocketDisconnect if client disconnects
                data = await websocket.receive_text()
                logger.debug(f"📨 Received from client {client_id}: {data}")

                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Invalid JSON from client {client_id}")
                    continue

                if payload.get("type") == "subscribe_request":
                    agent_id = str(payload.get("agent_id") or "").strip()
                    symbol = str(payload.get("symbol") or "").strip().upper()
                    interval = str(payload.get("interval") or "").strip()
                    timeframe_days = int(payload.get("timeframe_days") or 1)

                    if not agent_id or not symbol or not interval:
                        await websocket.send_json({
                            "type": "error",
                            "agent_id": agent_id,
                            "code": "INVALID_REQUEST",
                            "message": "agent_id, symbol, and interval are required",
                        })
                        continue

                    connection = agent_manager.get_connection(agent_id)
                    if not connection:
                        await websocket.send_json({
                            "type": "error",
                            "agent_id": agent_id,
                            "code": "AGENT_NOT_FOUND",
                            "message": f"No active connection for agent {agent_id}",
                        })
                        continue

                    subscription_id = f"{agent_id}:default"
                    logger.info(
                        "📨 Client %s requested subscribe: %s %s @ %s (%sd)",
                        client_id,
                        agent_id,
                        symbol,
                        interval,
                        timeframe_days,
                    )

                    await connection.subscribe(
                        subscription_id=subscription_id,
                        symbol=symbol,
                        interval=interval,
                        params={"timeframe_days": timeframe_days},
                    )
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"❌ Error in WebSocket connection {client_id}: {e}")
    finally:
        if heartbeat_task:
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.debug(f"💓 Heartbeat sent to client {id(websocket)}")
        except Exception as e:
            logger.debug(f"Heartbeat failed for client {id(websocket)}: {e}")
            break


async def broadcast_agent_message(agent_id: str, message: dict) -> None:
    """
    Broadcast ACP messages from agents to all connected frontend clients
    
    This is called by AgentConnection when messages are received from agents.
    """
    if not active_connections:
        logger.debug(f"No active connections to broadcast to")
        return
    
    message_type = message.get("type")
    logger.info(f"📤 Broadcasting {message_type} from {agent_id} to {len(active_connections)} client(s)")
    
    # Handle heartbeat messages - update status and notify frontend
    if message_type == "heartbeat":
        agent = agent_manager.get_agent(agent_id)
        if agent:
            agent.status.status = "online"
            agent.status.last_activity_ts = datetime.now(timezone.utc).isoformat()
            agent.status.error_message = None
        
        # Send agent status update to frontend
        status_message = {
            "type": "agent_status_update",
            "agent_id": agent_id,
            "status": "online",
            "error_message": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        disconnected = set()
        for websocket in active_connections:
            try:
                await websocket.send_json(status_message)
            except Exception as e:
                logger.debug(f"Failed to send status update: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            active_connections.discard(ws)
    
    # Forward snapshot messages (historical data) to frontend
    elif message_type == "snapshot":
        logger.info(f"📸 Broadcasting snapshot from {agent_id} with {message.get('count', 0)} bars")
        disconnected = set()
        for websocket in active_connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send snapshot: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            active_connections.discard(ws)
    
    # Forward data messages to frontend
    elif message_type == "data":
        disconnected = set()
        for websocket in active_connections:
            try:
                # Wrap in ACP envelope and forward
                await websocket.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to forward data message: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            active_connections.discard(ws)
    
    # Forward error messages
    elif message_type == "error":
        logger.error(f"❌ Agent {agent_id} error: {message.get('code')} - {message.get('message')}")
        
        disconnected = set()
        for websocket in active_connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to forward error message: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            active_connections.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
