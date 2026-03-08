"""
Odin Backend - FastAPI WebSocket Server (ACP v0.2.0)

Manages WebSocket connections from the frontend and routes them to ACP agents:
- Accepts WebSocket connections from the frontend (each is a client_id)
- Loads agent configurations from overlay_agents.yaml (ACP v0.2.0)
- Connects to ACP agents via WebSocket
- Routes ACP messages to specific sessions (not broadcast-all)
- Maintains canonical candle store per session with deduplication
- Provides REST API for agent management
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent_data_store import SessionDataStore
from app.agent_manager import agent_manager
from app.models import SessionManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global session management
session_manager = SessionManager()

# Track active WebSocket connections: client_id -> WebSocket
active_connections: dict[str, WebSocket] = {}

# Track session data stores: session_id -> SessionDataStore
session_data_stores: dict[str, SessionDataStore] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle"""
    logger.info("🚀 Odin backend starting (ACP v0.2.0)...")
    
    # Load agent configurations from YAML
    config_path = Path(__file__).parent.parent.parent / "overlay_agents.yaml"
    logger.info(f"📂 Loading agent configs from: {config_path}")
    agent_manager.load_from_yaml(config_path)
    
    # Set up message callback to forward ACP messages to the right session
    agent_manager.on_agent_message = route_agent_message
    
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
    description="Trading platform backend - ACP v0.2.0 session router",
    version="0.2.0",
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
        "version": "0.2.0",
        "status": "running",
        "acp_version": "0.2.0",
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ACP v0.2.0 WebSocket endpoint for frontend connections.
    
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
            "acp_version": "0.2.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Connected to Odin backend (ACP v0.2.0)"
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
        
        # Clean up session data stores
        for session_id in deleted_sessions:
            if session_id in session_data_stores:
                del session_data_stores[session_id]
        
        active_connections.pop(client_id, None)
        logger.info(f"❌ Client {client_id} disconnected. Cleaned up {len(deleted_sessions)} session(s). Total connections: {len(active_connections)}")


async def handle_subscribe_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict
) -> None:
    """Handle a subscription request from the frontend (ACP v0.2.0)"""
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
    
    # Create session in SessionManager
    session = session_manager.create_session(client_id, agent_id, symbol, interval)
    logger.info(f"📊 Created session {session_id} for client {client_id}: {agent_id} {symbol} @ {interval}")
    
    # Create SessionDataStore for this session
    data_store = SessionDataStore(
        session_id=session_id,
        agent_id=agent_id,
        symbol=symbol,
        interval=interval
    )
    data_store.update_retention(timeframe_days, interval)
    session_data_stores[session_id] = data_store
    
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
        "acp_version": "0.2.0",
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


async def send_heartbeats(websocket: WebSocket, client_id: str):
    """Send periodic heartbeat messages to keep connection alive"""
    while True:
        try:
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
            await websocket.send_json({
                "type": "heartbeat",
                "acp_version": "0.2.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.debug(f"💓 Heartbeat sent to {client_id}")
        except Exception as e:
            logger.debug(f"Heartbeat failed for {client_id}: {e}")
            break


async def route_agent_message(agent_id: str, session_id: str, message: dict) -> None:
    """
    Route ACP messages from agents to the appropriate session's WebSocket.
    
    ACP v0.2.0: Messages include session_id for routing.
    """
    message_type = message.get("type")
    
    # Find the session and its client
    session = session_manager.get_session(session_id)
    if not session:
        logger.debug(f"Session {session_id} not found, message dropped")
        return
    
    client_id = session.client_id
    websocket = active_connections.get(client_id)
    if not websocket:
        logger.debug(f"Client {client_id} not connected, message dropped")
        return
    
    # Process message based on type
    if message_type == "heartbeat":
        # Update agent status and forward
        agent = agent_manager.get_agent(agent_id)
        if agent:
            agent.status.status = "online"
            agent.status.last_activity_ts = datetime.now(timezone.utc).isoformat()
            agent.status.error_message = None
        
        status_message = {
            "type": "heartbeat",
            "agent_id": agent_id,
            "session_id": session_id,
            "status": "online",
            "acp_version": "0.2.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            await websocket.send_json(status_message)
            logger.debug(f"💓 Forwarded heartbeat to {client_id} for session {session_id}")
        except Exception as e:
            logger.debug(f"Failed to send heartbeat to {client_id}: {e}")
    
    elif message_type == "data" and message.get("schema") == "ohlc":
        # Ingest OHLC record into session data store
        record = message.get("record")
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_ohlc(record)
            
            if result:
                # Record was ingested (not a duplicate)
                message["record"] = result
                logger.info(
                    f"📥 OHLC ingested for session {session_id}: id={result.get('id')} rev={result.get('rev')} state={result.get('bar_state')}"
                )
            else:
                # Duplicate record, skip
                logger.debug(f"⏭️  Duplicate OHLC skipped for session {session_id}")
                return
        
        # Forward to client
        try:
            await websocket.send_json(message)
            logger.debug(f"📤 Forwarded OHLC data to {client_id} for session {session_id}")
        except Exception as e:
            logger.debug(f"Failed to forward data to {client_id}: {e}")
    
    elif message_type == "candle_correction":
        # Handle candle correction (upsert with higher rev)
        record = message.get("record")
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_ohlc(record)
            
            if result:
                message["record"] = result
                logger.info(f"🔄 Candle correction ingested for session {session_id}: id={result.get('id')} rev={result.get('rev')}")
            else:
                logger.debug(f"⏭️  Candle correction skipped for session {session_id} (lower rev)")
                return
        
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to forward candle_correction to {client_id}: {e}")
    
    elif message_type == "error":
        # Forward error message
        logger.error(f"❌ Agent {agent_id} error: {message.get('code')} - {message.get('message')}")
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to forward error to {client_id}: {e}")
    
    else:
        # Forward other message types (tick_update, history_push, etc.)
        try:
            await websocket.send_json(message)
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
