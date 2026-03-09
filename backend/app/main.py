"""
Odin Backend - FastAPI WebSocket Server (ACP v0.3.0)

Manages WebSocket connections from the frontend and routes them to ACP agents:
- Accepts WebSocket connections from the frontend (each is a client_id)
- Loads agent configurations from overlay_agents.yaml (ACP v0.3.0)
- Connects to ACP agents via WebSocket
- Routes ACP messages to specific sessions (not broadcast-all)
- Maintains canonical candle store per session with deduplication
- Provides REST API for agent management
"""

from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Set

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent_data_store import SessionDataStore
from app.agent_manager import agent_manager
from app.models import AgentConfig, SessionManager, Variable, build_variable_name
from app.workspace_store import WorkspaceStore

ACP_SPEC_VERSION = "ACP-0.3.0"
ACP_API_VERSION = "0.3.0"
MAX_HISTORY_PUSH_CANDLES = 1500
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

# Track backend-assigned stream sequence numbers: session_id -> latest_seq
session_seq_counters: dict[str, int] = {}

# Track outbound sequence numbers sent from backend -> indicator agent:
# (indicator_agent_id, session_id) -> latest_seq
indicator_seq_counters: dict[tuple[str, str], int] = {}

# Workspace persistence store
workspace_store: WorkspaceStore | None = None


def get_workspace_store() -> WorkspaceStore:
    if workspace_store is None:
        raise HTTPException(status_code=500, detail="Workspace store not initialized")
    return workspace_store


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
    global workspace_store
    logger.info(f"🚀 Odin backend starting ({ACP_SPEC_VERSION})...")

    workspace_db_path = Path(__file__).parent.parent / "data" / "user_config.db"
    workspace_store = WorkspaceStore(workspace_db_path)
    
    # Load agent configurations from YAML
    config_path = Path(__file__).parent.parent.parent / "overlay_agents.yaml"
    logger.info(f"📂 Loading agent configs from: {config_path}")
    agent_manager.load_from_yaml(config_path)
    agent_manager.config_file_path = config_path  # Store path for persistence
    
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
    description="Trading platform backend - ACP v0.3.0 session router",
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


async def fetch_agent_metadata(agent_url: str) -> dict:
    normalized_url = agent_url.rstrip("/")
    metadata_url = f"{normalized_url}/metadata"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(metadata_url)
        response.raise_for_status()
    metadata = response.json()

    if metadata.get("spec_version") != ACP_SPEC_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Incompatible spec_version: {metadata.get('spec_version')} (expected {ACP_SPEC_VERSION})",
        )

    if metadata.get("agent_type") not in {"price", "indicator", "event"}:
        raise HTTPException(status_code=400, detail="Invalid agent_type in metadata")

    outputs = metadata.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise HTTPException(status_code=400, detail="Metadata outputs[] is required")

    if metadata.get("agent_type") == "indicator":
        indicators = metadata.get("indicators")
        if not isinstance(indicators, list) or not indicators:
            raise HTTPException(status_code=400, detail="Indicator agents must expose indicators[]")

    return metadata


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
    runtime_config = request.params or {}
    outputs = metadata.get("outputs") or []
    description = metadata.get("description", "")

    final_agent_id = base_agent_id
    final_name = metadata["agent_name"]

    if agent_type == "indicator":
        if not request.indicator_id:
            raise HTTPException(status_code=400, detail="indicator_id is required for indicator agents")
        indicators = metadata.get("indicators") or []
        selected_indicator = next((item for item in indicators if item.get("indicator_id") == request.indicator_id), None)
        if not selected_indicator:
            raise HTTPException(status_code=400, detail=f"Unknown indicator_id: {request.indicator_id}")
        indicator_base_agent_id = f"{base_agent_id}__{request.indicator_id}"
        final_agent_id, instance_index = next_available_indicator_instance(indicator_base_agent_id)
        final_name = f"{metadata['agent_name']} - {selected_indicator.get('name', request.indicator_id)}"
        if instance_index > 1:
            final_name = f"{final_name} ({instance_index})"
        outputs = selected_indicator.get("outputs") or outputs
        description = selected_indicator.get("description") or description

    agent_config = AgentConfig(
        spec_version=ACP_SPEC_VERSION,
        agent_url=request.agent_url.rstrip("/"),
        agent_id=final_agent_id,
        agent_name=final_name,
        agent_version=metadata["agent_version"],
        description=description,
        agent_type=agent_type,
        config_schema=runtime_config,
        outputs=outputs,
        indicators=metadata.get("indicators") or [],
    )

    agent = agent_manager.add_or_update_agent(agent_config)

    existing_connection = agent_manager.get_connection(final_agent_id)
    if not existing_connection:
        from app.agent_connection import AgentConnection

        connection = AgentConnection(agent=agent, on_message=route_agent_message)
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
            if len(canonical_candles) > MAX_HISTORY_PUSH_CANDLES:
                candles_for_indicator = candles_for_indicator[-MAX_HISTORY_PUSH_CANDLES:]

            await connection.send_history_push(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                candles=candles_for_indicator,
            )

    # Persist agents to YAML after adding
    if agent_manager.config_file_path:
        agent_manager.persist_agents_to_yaml(agent_manager.config_file_path)

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

    updated_config = dict(agent.config.config_schema or {})
    updated_config.update(request.params or {})

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
    )

    updated_agent = agent_manager.add_or_update_agent(updated_agent_config)

    connection = agent_manager.get_connection(agent_id)
    if connection and updated_agent.config.agent_type == "indicator":
        subscriptions = list(connection.subscriptions.items())
        for session_id, subscription in subscriptions:
            symbol = str(subscription.get("symbol") or "")
            interval = str(subscription.get("interval") or "")
            if not symbol or not interval:
                continue

            await connection.subscribe(
                session_id=session_id,
                symbol=symbol,
                interval=interval,
                params=updated_config,
            )

            if session_id in session_data_stores:
                canonical_candles = session_data_stores[session_id].get_canonical_candles()
                if canonical_candles:
                    candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]
                    if len(canonical_candles) > MAX_HISTORY_PUSH_CANDLES:
                        candles_for_indicator = candles_for_indicator[-MAX_HISTORY_PUSH_CANDLES:]

                    await connection.send_history_push(
                        session_id=session_id,
                        symbol=symbol,
                        interval=interval,
                        candles=candles_for_indicator,
                    )

    # Persist agents to YAML after updating
    if agent_manager.config_file_path:
        agent_manager.persist_agents_to_yaml(agent_manager.config_file_path)

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
    return workspace


@app.post("/api/workspaces/{workspace_name}/activate")
async def activate_workspace(workspace_name: str):
    store = get_workspace_store()
    workspace = store.get_workspace(workspace_name)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    store.set_active_workspace(workspace_name)
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
            
            # For simple schemas (line, histogram), create one variable
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ACP v0.3.0 WebSocket endpoint for frontend connections.
    
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
            "message": "Connected to Odin backend (ACP v0.3.0)"
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
            for key in [k for k in indicator_seq_counters.keys() if k[1] == session_id]:
                indicator_seq_counters.pop(key, None)
        
        active_connections.pop(client_id, None)
        logger.info(f"❌ Client {client_id} disconnected. Cleaned up {len(deleted_sessions)} session(s). Total connections: {len(active_connections)}")


async def handle_subscribe_request(
    websocket: WebSocket,
    client_id: str,
    payload: dict
) -> None:
    """Handle a subscription request from the frontend (ACP v0.3.0)"""
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
    
    # Use frontend-provided session_id (ACP v0.3.0: frontend owns session_id)
    # Create or retrieve session in SessionManager
    session = session_manager.create_session(
        session_id=session_id,
        client_id=client_id,
        agent_id=agent_id,
        symbol=symbol,
        interval=interval
    )
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
        indicator_subscribe_result = await indicator_connection.subscribe(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            params=indicator_config.config_schema,
        )

        if indicator_subscribe_result and canonical_candles:
            candles_for_indicator = [to_indicator_ohlc(candle) for candle in canonical_candles]
            if len(canonical_candles) > MAX_HISTORY_PUSH_CANDLES:
                candles_for_indicator = candles_for_indicator[-MAX_HISTORY_PUSH_CANDLES:]
                logger.info(
                    "📉 Trimming history_push for %s: %s -> %s candles",
                    indicator_config.agent_id,
                    len(canonical_candles),
                    len(candles_for_indicator),
                )

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
    
    ACP v0.3.0: Messages include session_id for routing.
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
        if agent:
            agent.status.status = "online"
            agent.status.last_activity_ts = datetime.now(timezone.utc).isoformat()
            agent.status.error_message = None
        
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
                logger.info(
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
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to forward error to {client_id}: {e}")
    
    elif message_type == "history_response":
        # Overlay agent response to history_push
        original_agent_id = message.get("agent_id", "not_set")
        logger.info(f"📚 Received history_response from agent connection {agent_id}, message.agent_id was '{original_agent_id}' for session {session_id}")
        overlays = message.get("overlays", [])
        
        # Ingest overlay records into session data store
        if session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            for overlay_record in overlays:
                # Non-OHLC records use simple id-based dedup
                result = data_store.ingest_non_ohlc(overlay_record, source_agent_id=agent_id)
                if result:
                    logger.debug(f"📥 Overlay record ingested: id={result.get('id')}")
        
        # Ensure agent_id is set to the runtime instance ID
        message["agent_id"] = agent_id
        logger.info(f"📚 Set message agent_id to runtime instance ID: {agent_id}")
        
        # Forward to frontend
        try:
            replay_message = buffer_replay_message(session, message)
            logger.info(f"📤 Forwarding history_response: replay_message agent_id={replay_message.get('agent_id')}")
            await websocket.send_json(replay_message)
            logger.info(f"📤 Forwarded history_response with {len(overlays)} overlays to {client_id} (agent_id={replay_message.get('agent_id')})")
        except Exception as e:
            logger.debug(f"Failed to forward history_response to {client_id}: {e}")
    
    elif message_type == "overlay_update":
        # Live overlay value update from overlay agent
        original_agent_id = message.get("agent_id", "not_set")
        logger.info(f"📊 Received overlay_update from agent connection {agent_id}, message.agent_id was '{original_agent_id}' for session {session_id}")
        record = message.get("record")
        schema = message.get("schema")
        
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_non_ohlc(record, source_agent_id=agent_id)
            
            if result:
                message["record"] = result
                logger.info(
                    f"📥 Overlay update ingested for session {session_id}: "
                    f"schema={schema} id={result.get('id')}"
                )
            else:
                logger.debug(f"⏭️  Duplicate overlay update skipped for session {session_id}")
                return
        
        # Ensure agent_id is set to the runtime instance ID
        message["agent_id"] = agent_id
        logger.info(f"📊 Set message agent_id to runtime instance ID: {agent_id}")
        
        # Forward to frontend
        try:
            replay_message = buffer_replay_message(session, message)
            logger.info(f"📤 Forwarding overlay_update: replay_message agent_id={replay_message.get('agent_id')}")
            await websocket.send_json(replay_message)
            logger.debug(f"📤 Forwarded overlay_update to {client_id} for session {session_id} (agent_id={replay_message.get('agent_id')})")
        except Exception as e:
            logger.debug(f"Failed to forward overlay_update to {client_id}: {e}")
    
    elif message_type == "overlay_marker":
        # Event marker from overlay agent
        record = message.get("record")
        
        if isinstance(record, dict) and session_id in session_data_stores:
            data_store = session_data_stores[session_id]
            result = data_store.ingest_non_ohlc(record, source_agent_id=agent_id)
            
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
