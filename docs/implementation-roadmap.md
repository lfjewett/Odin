# Odin Backend Implementation Roadmap

**Purpose**: Align current Odin implementation with architecture outlined in `trading_platform_brief.docx.md`

**Status**: Current implementation is ~55-65% aligned. This roadmap closes the gaps.

---

## Current State Assessment

### ✅ What's Working
- ACP-0.1.0 base protocol implemented (subscribe, data, heartbeat, error)
- WebSocket connections to agents via `AgentConnection`
- Historical backfill via agent REST `/history` endpoint
- In-memory candle normalization per agent (`AgentDataStore`)
- Frontend receives snapshots and live updates
- Basic multi-client broadcast

### ❌ Critical Gaps
1. **No session isolation**: All clients receive all agent messages
2. **Backend doesn't own candles**: Each agent connection has its own store
3. **No sequence tracking/resync**: Missing reliability primitives
4. **Only price agents connect**: Overlay agents excluded (`output_schema == "ohlc"` filter)
5. **No bidirectional agent flow**: Can't push candles to indicator agents
6. **Protocol drift**: Multiple conflicting protocol docs

---

## Phase 1: Session Management & Canonical Candle Store

**Goal**: Backend owns one canonical candle array per session. All agents in that session work from identical data.

### 1.1 Data Structures

**File**: `backend/app/models.py`

Add:
```python
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

class Session(BaseModel):
    """Represents one frontend chart window"""
    session_id: str = Field(description="Unique session identifier (UUID)")
    ticker: str = Field(description="Symbol (e.g., SPY)")
    interval: str = Field(description="Candle interval (1m, 5m, etc)")
    timeframe_days: int = Field(default=7, description="History window in days")
    
    # Canonical candle storage - backend owns this
    candles: deque[Dict[str, Any]] = Field(default_factory=lambda: deque(maxlen=5000))
    candles_by_id: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    
    # Agent subscriptions for this session
    agent_subscriptions: Dict[str, str] = Field(default_factory=dict)  # agent_id -> subscription_id
    
    # Reliability tracking
    seq: int = Field(default=0, description="Monotonic sequence counter")
    message_buffer: deque[Dict[str, Any]] = Field(default_factory=lambda: deque(maxlen=100))
    
    # State
    state: Literal["initializing", "ready", "error"] = "initializing"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

**File**: `backend/app/session_manager.py` (NEW)

```python
"""
Session management for Odin backend.
Each session represents one frontend chart window.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Set, Callable, Any
from uuid import uuid4

from app.models import Session, Agent
from app.agent_connection import AgentConnection

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages chart sessions and their agent subscriptions"""
    
    def __init__(self):
        self.sessions: Dict[str, Session] = {}
        self.session_websockets: Dict[str, Set[Any]] = {}  # session_id -> set of WebSocket connections
        self.agent_connections: Dict[str, AgentConnection] = {}  # agent_id -> connection (shared across sessions)
    
    def create_session(
        self,
        ticker: str,
        interval: str,
        timeframe_days: int,
        agent_ids: list[str]
    ) -> str:
        """Create new session and return session_id"""
        session_id = str(uuid4())
        
        session = Session(
            session_id=session_id,
            ticker=ticker,
            interval=interval,
            timeframe_days=timeframe_days
        )
        
        # Register agent subscriptions for this session
        for agent_id in agent_ids:
            subscription_id = f"{session_id}:{agent_id}"
            session.agent_subscriptions[agent_id] = subscription_id
        
        self.sessions[session_id] = session
        self.session_websockets[session_id] = set()
        
        logger.info(f"Created session {session_id}: {ticker} @ {interval} ({timeframe_days}d)")
        return session_id
    
    def add_client_to_session(self, session_id: str, websocket: Any) -> bool:
        """Register a frontend WebSocket with a session"""
        if session_id not in self.sessions:
            return False
        
        self.session_websockets[session_id].add(websocket)
        logger.info(f"Client {id(websocket)} joined session {session_id}")
        return True
    
    def remove_client_from_session(self, session_id: str, websocket: Any):
        """Unregister frontend WebSocket from session"""
        if session_id in self.session_websockets:
            self.session_websockets[session_id].discard(websocket)
            
            # Clean up session if no clients remain
            if len(self.session_websockets[session_id]) == 0:
                logger.info(f"No clients remain for session {session_id}, cleaning up...")
                self.cleanup_session(session_id)
    
    def cleanup_session(self, session_id: str):
        """Remove session and unsubscribe from all agents"""
        if session_id not in self.sessions:
            return
        
        session = self.sessions[session_id]
        
        # Unsubscribe from all agents
        for agent_id, subscription_id in session.agent_subscriptions.items():
            if agent_id in self.agent_connections:
                # Note: unsubscribe is async, would need to await this properly
                asyncio.create_task(
                    self.agent_connections[agent_id].unsubscribe(subscription_id)
                )
        
        del self.sessions[session_id]
        del self.session_websockets[session_id]
        logger.info(f"Session {session_id} cleaned up")
    
    async def broadcast_to_session(self, session_id: str, message: Dict[str, Any]):
        """Send message to all clients in a session"""
        if session_id not in self.session_websockets:
            return
        
        disconnected = set()
        for websocket in self.session_websockets[session_id]:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send to client in session {session_id}: {e}")
                disconnected.add(websocket)
        
        # Clean up disconnected clients
        for ws in disconnected:
            self.remove_client_from_session(session_id, ws)
    
    def ingest_candle_to_session(self, session_id: str, candle: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add/update candle in session's canonical store.
        Returns the normalized candle.
        """
        if session_id not in self.sessions:
            logger.error(f"Session {session_id} not found")
            return candle
        
        session = self.sessions[session_id]
        candle_id = str(candle.get("id", ""))
        
        # Normalize revision
        previous = session.candles_by_id.get(candle_id)
        if isinstance(candle.get("rev"), int):
            rev = candle["rev"]
        elif previous and isinstance(previous.get("rev"), int):
            rev = previous["rev"] + 1
        else:
            rev = 0
        
        candle["rev"] = rev
        session.candles_by_id[candle_id] = candle
        
        # Add to rolling deque if finalized
        if candle.get("bar_state") == "final":
            session.candles.append(candle)
        
        return candle


# Global session manager instance
session_manager = SessionManager()
```

### 1.2 Backend Changes

**File**: `backend/app/main.py`

Changes:
1. Remove global `active_connections` broadcast
2. Add session creation on frontend connection with config
3. Route messages by `session_id`
4. Update `broadcast_agent_message` to route to specific session

Key modifications:
```python
from app.session_manager import session_manager

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = id(websocket)
    session_id: str | None = None
    
    try:
        # Wait for session config from frontend
        data = await websocket.receive_text()
        payload = json.loads(data)
        
        if payload.get("type") == "create_session":
            # Frontend sends: {type: "create_session", ticker, interval, timeframe_days, agent_ids}
            session_id = session_manager.create_session(
                ticker=payload["ticker"],
                interval=payload["interval"],
                timeframe_days=payload.get("timeframe_days", 7),
                agent_ids=payload.get("agent_ids", [])
            )
            
            session_manager.add_client_to_session(session_id, websocket)
            
            # Confirm session created
            await websocket.send_json({
                "type": "session_created",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # Initialize session: fetch history from price agent, push to overlay agents
            await initialize_session(session_id)
        
        # Handle messages
        while True:
            data = await websocket.receive_text()
            # Process commands...
    
    finally:
        if session_id:
            session_manager.remove_client_from_session(session_id, websocket)
```

### 1.3 Frontend Changes

**File**: `frontend/src/stream/useEventStream.ts`

Add session initialization:
```typescript
// On connect, create session
const createSession = () => {
  const payload = {
    type: "create_session",
    ticker: selectedSymbol,
    interval: selectedInterval,
    timeframe_days: selectedTimeframe,
    agent_ids: ["price_agent", "ema_agent", "rsi_agent"]  // from user config
  };
  
  ws.send(JSON.stringify(payload));
};

ws.onopen = () => {
  createSession();
  // Wait for session_created confirmation before marking connected
};
```

---

## Phase 2: Sequence Tracking & Reliability

**Goal**: Detect message gaps, replay missed messages, handle corrections

### 2.1 Message Sequencing

**File**: `backend/app/session_manager.py`

Add to `SessionManager`:
```python
def next_seq(self, session_id: str) -> int:
    """Get next sequence number and buffer message"""
    session = self.sessions.get(session_id)
    if not session:
        return 0
    
    session.seq += 1
    return session.seq

def buffer_message(self, session_id: str, message: Dict[str, Any]):
    """Store message in replay buffer"""
    session = self.sessions.get(session_id)
    if not session:
        return
    
    session.message_buffer.append(message)

async def handle_resync_request(
    self,
    session_id: str,
    last_seq_received: int
):
    """Replay messages from last known good sequence"""
    session = self.sessions.get(session_id)
    if not session:
        return
    
    # Find messages after last_seq_received
    replay_messages = [
        msg for msg in session.message_buffer
        if msg.get("seq", 0) > last_seq_received
    ]
    
    if len(replay_messages) > 0:
        # Send RESYNC_RESPONSE with batched messages
        await self.broadcast_to_session(session_id, {
            "type": "resync_response",
            "session_id": session_id,
            "last_seq": last_seq_received,
            "messages": replay_messages
        })
    else:
        # Gap too large, trigger full history reload
        await self.broadcast_to_session(session_id, {
            "type": "resync_failed",
            "session_id": session_id,
            "reason": "Gap too large, reload required"
        })
```

### 2.2 Frontend Gap Detection

**File**: `frontend/src/stream/useEventStream.ts`

```typescript
const lastSeqRef = useRef<number>(0);

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  const seq = message.seq;
  
  if (seq && seq !== lastSeqRef.current + 1) {
    // Gap detected!
    console.warn(`[WebSocket] Gap detected: expected ${lastSeqRef.current + 1}, got ${seq}`);
    
    // Request resync
    ws.send(JSON.stringify({
      type: "resync_request",
      session_id: currentSessionId,
      last_seq_received: lastSeqRef.current
    }));
  }
  
  if (seq) {
    lastSeqRef.current = seq;
  }
  
  // Process message...
};
```

---

## Phase 3: Bidirectional Agent Flow (Push Candles to Overlay Agents)

**Goal**: Backend pushes canonical candles to indicator/overlay agents. Agents compute and return overlays.

### 3.1 Extended ACP Messages

Implement new message types per ACP enhancement spec (see separate doc):

**Backend -> Agent**:
- `HISTORY_PUSH`: Full candle array on session start
- `TICK_UPDATE`: Live quote update (candle forming)
- `CANDLE_CLOSED`: Candle officially closed
- `CANDLE_CORRECTION`: Past candle was reconciled/changed

**Agent -> Backend**:
- `HISTORY_RESPONSE`: Computed overlay for historical range
- `OVERLAY_UPDATE`: New computed value after tick/candle
- `OVERLAY_MARKER`: Discrete event (signal, pattern, news)

### 3.2 Backend Push Implementation

**File**: `backend/app/session_manager.py`

```python
async def push_history_to_agents(self, session_id: str):
    """Push canonical history to all non-price agents in session"""
    session = self.sessions.get(session_id)
    if not session:
        return
    
    for agent_id, subscription_id in session.agent_subscriptions.items():
        agent = agent_manager.get_agent(agent_id)
        if not agent or agent.config.output_schema == "ohlc":
            continue  # Skip price agents
        
        connection = agent_manager.get_connection(agent_id)
        if not connection:
            continue
        
        # Send HISTORY_PUSH
        await connection.send_message({
            "type": "history_push",
            "spec_version": "ACP-0.2.0",
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": agent_id,
            "symbol": session.ticker,
            "interval": session.interval,
            "candles": list(session.candles),
            "seq": session.seq
        })

async def push_tick_to_agents(self, session_id: str, candle: Dict[str, Any]):
    """Push live tick update to overlay agents"""
    session = self.sessions.get(session_id)
    if not session:
        return
    
    seq = self.next_seq(session_id)
    
    for agent_id, subscription_id in session.agent_subscriptions.items():
        agent = agent_manager.get_agent(agent_id)
        if not agent or agent.config.output_schema == "ohlc":
            continue
        
        connection = agent_manager.get_connection(agent_id)
        if not connection:
            continue
        
        message = {
            "type": "tick_update",
            "spec_version": "ACP-0.2.0",
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": agent_id,
            "candle": candle,
            "seq": seq
        }
        
        self.buffer_message(session_id, message)
        await connection.send_message(message)

async def push_candle_closed_to_agents(self, session_id: str, candle: Dict[str, Any]):
    """Notify agents that a candle has closed"""
    session = self.sessions.get(session_id)
    if not session:
        return
    
    seq = self.next_seq(session_id)
    
    for agent_id, subscription_id in session.agent_subscriptions.items():
        agent = agent_manager.get_agent(agent_id)
        if not agent or agent.config.output_schema == "ohlc":
            continue
        
        connection = agent_manager.get_connection(agent_id)
        if not connection:
            continue
        
        message = {
            "type": "candle_closed",
            "spec_version": "ACP-0.2.0",
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": agent_id,
            "candle": candle,
            "seq": seq
        }
        
        self.buffer_message(session_id, message)
        await connection.send_message(message)
```

### 3.3 Agent Response Handling

**File**: `backend/app/agent_connection.py`

Update `listen()` to handle overlay responses:
```python
async def listen(self) -> None:
    """Listen for messages from agent"""
    # ... existing code ...
    
    message_type = message.get("type")
    
    if message_type == "history_response":
        # Agent computed overlays for historical range
        session_id = message.get("session_id")
        if session_id:
            await session_manager.broadcast_to_session(session_id, {
                "type": "overlay_history",
                "agent_id": self.agent_id,
                "subscription_id": message.get("subscription_id"),
                "overlays": message.get("overlays", [])
            })
    
    elif message_type == "overlay_update":
        # Agent computed new overlay value
        session_id = message.get("session_id")
        if session_id:
            await session_manager.broadcast_to_session(session_id, message)
    
    elif message_type == "overlay_marker":
        # Discrete event from agent
        session_id = message.get("session_id")
        if session_id:
            await session_manager.broadcast_to_session(session_id, message)
```

### 3.4 Remove Output Schema Filter

**File**: `backend/app/agent_manager.py`

Change `start_all_connections`:
```python
async def start_all_connections(self) -> None:
    """Start connections to ALL configured agents"""
    for agent in self.agents.values():
        # REMOVE THIS FILTER:
        # if agent.config.output_schema == "ohlc":
        
        # Connect to all agents
        connection = AgentConnection(agent=agent, on_message=self.on_agent_message)
        self.add_connection(agent.agent_id, connection)
        await connection.start()
```

---

## Phase 4: Agent Metadata & Dynamic Configuration

**Goal**: Query agent `/metadata` endpoint before subscribing. Use `config_schema` for validation.

### 4.1 Metadata Fetch

**File**: `backend/app/agent_manager.py`

```python
async def fetch_agent_metadata(self, agent_id: str) -> Dict[str, Any] | None:
    """Fetch metadata from agent's /metadata endpoint"""
    agent = self.get_agent(agent_id)
    if not agent:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{agent.config.agent_url}/metadata")
            response.raise_for_status()
            metadata = response.json()
            
            # Update agent config with metadata
            agent.config.config_schema = metadata.get("config_schema", {})
            agent.config.output_schema = metadata.get("output_schema", "unknown")
            
            logger.info(f"Fetched metadata for {agent_id}: {metadata.get('agent_name')}")
            return metadata
    except Exception as e:
        logger.error(f"Failed to fetch metadata for {agent_id}: {e}")
        return None

async def start_all_connections(self) -> None:
    """Start connections after fetching metadata"""
    for agent in self.agents.values():
        # Fetch metadata first
        metadata = await self.fetch_agent_metadata(agent.agent_id)
        if not metadata:
            logger.warning(f"Skipping {agent.agent_id} - no metadata")
            continue
        
        # Then connect
        connection = AgentConnection(agent=agent, on_message=self.on_agent_message)
        self.add_connection(agent.agent_id, connection)
        await connection.start()
```

---

## Phase 5: Protocol Documentation Cleanup

**Goal**: Single source of truth for protocol contract

### 5.1 Consolidate Docs

Keep only:
- `protocols/acp/spec/ACP.md` (enhanced with Odin extensions)
- Archive old docs:
  - Move `protocols/acp/protocol/ws.md` → `protocols/acp/archive/`
  - Move `protocols/acp/protocol/event-schema.md` → `protocols/acp/archive/`

### 5.2 Update README

**File**: `protocols/acp/README.md`

Update to reference only `spec/ACP.md` as canonical source.

---

## Implementation Order

1. **Week 1**: Phase 1 (Session management)
   - Day 1-2: Data structures (Session, SessionManager)
   - Day 3-4: Backend integration (main.py routing)
   - Day 5: Frontend session creation

2. **Week 2**: Phase 2 (Reliability)
   - Day 1-2: Sequence tracking and buffering
   - Day 3-4: Resync implementation
   - Day 5: Frontend gap detection

3. **Week 3**: Phase 3 (Bidirectional flow)
   - Day 1-2: HISTORY_PUSH, TICK_UPDATE, CANDLE_CLOSED
   - Day 3-4: HISTORY_RESPONSE, OVERLAY_UPDATE handling
   - Day 5: Test with example indicator agent

4. **Week 4**: Phase 4-5 (Polish)
   - Day 1-2: Metadata fetching
   - Day 3-4: Remove filters, config validation
   - Day 5: Documentation cleanup

---

## Testing Strategy

### Unit Tests
- `SessionManager` candle ingestion
- Sequence gap detection
- Message buffer replay

### Integration Tests
- Full session lifecycle (create → subscribe → stream → cleanup)
- Multi-agent overlay merging
- Gap recovery with resync

### End-to-End Tests
- Frontend creates session → backend fetches history → agents compute overlays → UI renders
- Network interruption → gap detected → resync → resume
- Multi-window sessions with different tickers

---

## Success Metrics

✅ Backend owns canonical candles per session  
✅ Multiple frontend windows isolated by session_id  
✅ Sequence gaps detected and recovered automatically  
✅ All agent types (ohlc, line, event) connect and stream  
✅ Overlay agents receive candle pushes and return computed results  
✅ Protocol documentation is consolidated and accurate  
✅ Zero message loss under normal operation  
✅ Graceful degradation under network issues  

---

## Risk Mitigation

**Risk**: State explosion with many sessions  
**Mitigation**: Aggressive session cleanup, configurable buffer sizes, memory monitoring

**Risk**: Slow overlay agents block candle distribution  
**Mitigation**: Non-blocking async sends, timeout handling per agent

**Risk**: Protocol versioning breaks existing agents  
**Mitigation**: Support both ACP-0.1.0 and ACP-0.2.0 during transition, reject unknown versions

**Risk**: Frontend doesn't handle out-of-order overlays  
**Mitigation**: Timestamp-based ordering in UI, clear "computing" states

---

## Future Enhancements (Post Phase 5)

- Persistent session recovery (write session state to Redis/DB)
- Backend-side indicator computation (optional optimization)
- Agent capability negotiation (compression, binary protocols)
- Multi-symbol sessions (portfolio view)
- Trading bot integration with same session infrastructure
