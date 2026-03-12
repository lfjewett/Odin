"""
Agent WebSocket Connection Manager (ACP v0.4.3)

Manages WebSocket connections to ACP agents and handles session-based subscriptions.
Each agent connection can serve multiple sessions (clients).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.models import Agent

logger = logging.getLogger(__name__)


ACP_SPEC_VERSION = "ACP-0.4.3"
COMPATIBLE_ACP_SPEC_VERSIONS = {"ACP-0.4.0", "ACP-0.4.1", "ACP-0.4.2", "ACP-0.4.3"}
DEFAULT_CHUNK_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RECORDS_PER_CHUNK = 5000
DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES = 10 * 1024 * 1024


class AgentConnection:
    """
    Manages WebSocket connection and subscriptions for a single agent.
    
    ACP v0.4.3: Supports multiple concurrent sessions per agent.
    Each session is isolated (session_id in all messages).
    """
    
    def __init__(
        self,
        agent: Agent,
        on_message: Callable[[str, str, dict[str, Any]], None] | None = None,
        on_rebootstrap: Callable[[str, str], Any] | None = None,
    ):
        """
        Args:
            agent: Agent configuration
            on_message: Callback(agent_id, session_id, message) for incoming messages
        """
        self.agent = agent
        self.on_message = on_message
        self.on_rebootstrap = on_rebootstrap
        self.websocket: WebSocketClientProtocol | None = None
        
        # Subscriptions: session_id -> subscription_info
        # Format: {
        #   "session_id": "...",
        #   "symbol": "SPY",
        #   "interval": "1m",
        #   "params": {...}
        # }
        self.subscriptions: dict[str, dict[str, Any]] = {}
        
        # Metadata from agent (fetched before first subscribe)
        self.metadata: dict[str, Any] | None = None
        self.metadata_fetched: bool = False
        
        self.running = False
        self.reconnect_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._subscription_version_by_session: dict[str, int] = {}
        
    @property
    def agent_id(self) -> str:
        return self.agent.agent_id
    
    @property
    def ws_url(self) -> str:
        """Build WebSocket URL from agent's base URL"""
        base_url = self.agent.config.agent_url.rstrip("/")
        # Convert http:// to ws://
        ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_base}/ws/live"
    
    @property
    def http_base_url(self) -> str:
        """Get HTTP base URL for REST API calls"""
        return self.agent.config.agent_url.rstrip("/")

    def _build_subscription_id(self, session_id: str) -> str:
        """Build the current per-connection subscription id for a session."""
        version = int(self._subscription_version_by_session.get(session_id, 0))
        if version <= 0:
            version = 1
            self._subscription_version_by_session[session_id] = version
        return f"{session_id}::{self.agent_id}::v{version}"

    def _next_subscription_id(self, session_id: str) -> str:
        """Advance and return a new subscription id for a session."""
        version = int(self._subscription_version_by_session.get(session_id, 0)) + 1
        self._subscription_version_by_session[session_id] = version
        return f"{session_id}::{self.agent_id}::v{version}"

    @staticmethod
    def _format_history_timestamp(value: datetime) -> str:
        """Format timestamps for agent /history queries as UTC ISO-8601 with Z suffix."""
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    
    async def fetch_metadata(self) -> bool:
        """
        Fetch and validate agent metadata (ACP v0.4.1).
        
        Must be called before first subscribe.
        Returns True if metadata is valid, False otherwise.
        """
        if self.metadata_fetched:
            return self.metadata is not None
        
        try:
            url = f"{self.http_base_url}/metadata"
            logger.info(f"[{self.agent_id}] Fetching metadata from {url}")
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                metadata = response.json()
                
                # Validate required fields per ACP v0.4.0
                required_fields = [
                    "spec_version",
                    "agent_id",
                    "agent_name",
                    "agent_version",
                    "description",
                    "agent_type",
                    "config_schema",
                    "outputs",
                    "transport_limits",
                ]
                
                missing_fields = [f for f in required_fields if f not in metadata]
                if missing_fields:
                    logger.error(
                        f"[{self.agent_id}] Metadata validation failed: missing fields {missing_fields}"
                    )
                    self.metadata_fetched = True
                    self.metadata = None
                    return False
                
                # Validate spec_version compatibility
                spec_version = metadata.get("spec_version")
                if spec_version not in COMPATIBLE_ACP_SPEC_VERSIONS:
                    logger.error(
                        f"[{self.agent_id}] Incompatible spec_version: {spec_version} (expected one of {sorted(COMPATIBLE_ACP_SPEC_VERSIONS)})"
                    )
                    self.metadata_fetched = True
                    self.metadata = None
                    return False
                
                # Validate agent_type (allow variations like "price_agent" for "price")
                agent_type = metadata.get("agent_type", "").lower()
                valid_types = ["price", "indicator", "event"]
                # Check if agent_type starts with any valid type
                if not any(agent_type.startswith(vtype) for vtype in valid_types):
                    logger.error(
                        f"[{self.agent_id}] Invalid agent_type: {metadata.get('agent_type')} "
                        f"(expected one of: {valid_types})"
                    )
                    self.metadata_fetched = True
                    self.metadata = None
                    return False
                
                # Normalize agent_type to standard value
                for vtype in valid_types:
                    if agent_type.startswith(vtype):
                        metadata["agent_type"] = vtype
                        agent_type = vtype
                        break

                transport_limits = metadata.get("transport_limits") or {}
                max_records_per_chunk = int(
                    transport_limits.get("max_records_per_chunk")
                    or DEFAULT_MAX_RECORDS_PER_CHUNK
                )
                max_websocket_message_bytes = int(
                    transport_limits.get("max_websocket_message_bytes")
                    or DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES
                )
                chunk_timeout_seconds = int(
                    transport_limits.get("chunk_timeout_seconds")
                    or DEFAULT_CHUNK_TIMEOUT_SECONDS
                )

                if max_records_per_chunk < 1000 or max_records_per_chunk > 10000:
                    logger.error(
                        f"[{self.agent_id}] Invalid transport_limits.max_records_per_chunk={max_records_per_chunk}"
                    )
                    self.metadata_fetched = True
                    self.metadata = None
                    return False

                if max_websocket_message_bytes < 1048576:
                    logger.error(
                        f"[{self.agent_id}] Invalid transport_limits.max_websocket_message_bytes={max_websocket_message_bytes}"
                    )
                    self.metadata_fetched = True
                    self.metadata = None
                    return False

                metadata["transport_limits"] = {
                    "max_records_per_chunk": max_records_per_chunk,
                    "max_websocket_message_bytes": max_websocket_message_bytes,
                    "chunk_timeout_seconds": chunk_timeout_seconds,
                }

                if agent_type == "indicator":
                    indicators = metadata.get("indicators")
                    if not isinstance(indicators, list) or not indicators:
                        logger.error(f"[{self.agent_id}] Indicator agent missing required indicators[] catalog")
                        self.metadata_fetched = True
                        self.metadata = None
                        return False
                
                # Store metadata
                self.metadata = metadata
                self.metadata_fetched = True
                
                logger.info(
                    f"[{self.agent_id}] ✅ Metadata validated: type={agent_type}, "
                    f"outputs={len(metadata.get('outputs', []))}, spec={spec_version}"
                )
                
                return True
                
        except httpx.TimeoutException:
            logger.error(f"[{self.agent_id}] ⏱️  Metadata fetch timeout")
            self.metadata_fetched = True
            self.metadata = None
            return False
        except httpx.HTTPError as e:
            logger.error(f"[{self.agent_id}] ❌ Metadata fetch HTTP error: {e}")
            self.metadata_fetched = True
            self.metadata = None
            return False
        except Exception as e:
            logger.error(f"[{self.agent_id}] ❌ Metadata fetch failed: {e}")
            self.metadata_fetched = True
            self.metadata = None
            return False
    
    async def connect(self) -> bool:
        """Establish WebSocket connection to agent"""
        if self.websocket and not self.websocket.closed:
            logger.warning(f"[{self.agent_id}] Already connected")
            return True
        
        try:
            logger.info(f"[{self.agent_id}] Connecting to {self.ws_url}...")
            ws_limit = DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES
            if self.metadata and self.metadata.get("transport_limits"):
                ws_limit = int(
                    self.metadata["transport_limits"].get("max_websocket_message_bytes")
                    or DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES
                )
            ws_limit = max(ws_limit, DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES)
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10,
                max_size=ws_limit,
            )
            logger.info(f"✅ [{self.agent_id}] Connected to agent WebSocket")
            self.running = True
            self.agent.status.status = "online"
            self.agent.status.error_message = None
            return True
            
        except Exception as e:
            logger.error(f"❌ [{self.agent_id}] Failed to connect: {e}")
            self.agent.status.status = "offline"
            self.agent.status.error_message = str(e)
            self.websocket = None
            return False
    
    async def disconnect(self) -> None:
        """Close WebSocket connection"""
        self.running = False
        
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
            self.reconnect_task = None
        
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"[{self.agent_id}] Error closing WebSocket: {e}")
            finally:
                self.websocket = None
        
        logger.info(f"🔌 [{self.agent_id}] Disconnected")
        self.agent.status.status = "offline"
    
    async def send_message(self, message: dict[str, Any]) -> bool:
        """Send a message to the agent"""
        if not self.websocket or self.websocket.closed:
            logger.error(f"[{self.agent_id}] Cannot send message: not connected")
            return False
        
        try:
            msg_type = message.get('type')
            logger.info(f"📤 [{self.agent_id}] Sending {msg_type} to agent (session: {message.get('session_id')})")
            async with self._send_lock:
                await self.websocket.send(json.dumps(message))
            logger.info(f"✅ [{self.agent_id}] Sent {msg_type} successfully")
            return True
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to send message: {e}")
            return False
    
    async def fetch_history(
        self,
        symbol: str,
        from_ts: str,
        to_ts: str,
        interval: str
    ) -> list[dict[str, Any]]:
        """Fetch historical data from agent's REST /history endpoint"""
        try:
            url = f"{self.http_base_url}/history"
            params = {
                "symbol": symbol,
                "from": from_ts,
                "to": to_ts,
                "interval": interval
            }
            
            logger.info(
                "[%s] Fetching history: %s %s from %s to %s",
                self.agent_id, symbol, interval, from_ts, to_ts
            )
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                
                data = response.json()
                bars = data.get("data", [])
                
                logger.info(f"[{self.agent_id}] ✅ Fetched {len(bars)} historical bars")
                return bars
                
        except httpx.TimeoutException:
            logger.error(f"[{self.agent_id}] ⏱️  History fetch timeout")
            return []
        except httpx.HTTPError as e:
            logger.error(f"[{self.agent_id}] ❌ History fetch HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"[{self.agent_id}] ❌ History fetch failed: {e}")
            return []
    
    async def subscribe(
        self,
        session_id: str,
        symbol: str,
        interval: str,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        """
        Subscribe to live data stream for a session.
        
        Args:
            session_id: Unique session identifier (client ID + chart view)
            symbol: Trade symbol (e.g., "SPY")
            interval: Candle interval (e.g., "1m")
            params: Optional parameters like timeframe_days
        """
        # ACP v0.4.0: Fetch and validate metadata before first subscribe
        if not self.metadata_fetched:
            logger.info(f"[{self.agent_id}] Fetching metadata before first subscribe...")
            metadata_valid = await self.fetch_metadata()
            if not metadata_valid:
                logger.error(f"[{self.agent_id}] Cannot subscribe: metadata validation failed")
                return False
        
        if not self.websocket or self.websocket.closed:
            logger.error(f"[{self.agent_id}] Cannot subscribe: not connected")
            return False

        normalized_params = params or {}
        selected_indicator_id = self.agent.config.selected_indicator_id
        existing = self.subscriptions.get(session_id)
        if existing and not force:
            existing_params = existing.get("params") or {}
            if (
                existing.get("symbol") == symbol
                and existing.get("interval") == interval
                and existing.get("indicator_id") == selected_indicator_id
                and existing_params == normalized_params
            ):
                logger.info(
                    f"[{self.agent_id}] Subscribe request unchanged for {session_id}, skipping"
                )
                return True
        
        # ACP v0.4.0: Include session_id and per-connection subscription_id.
        # Multiple indicator instances can share a session_id, so subscription_id
        # must be unique per connection to avoid sequence/state collisions.
        subscription_id = self._next_subscription_id(session_id)
        message = {
            "type": "subscribe",
            "spec_version": ACP_SPEC_VERSION,
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": self.agent_id,
            "symbol": symbol,
            "interval": interval,
            "params": normalized_params
        }
        if self.agent.config.agent_type == "indicator" and selected_indicator_id:
            message["indicator_id"] = selected_indicator_id
        
        success = await self.send_message(message)
        if success:
            self.subscriptions[session_id] = {
                "session_id": session_id,
                "subscription_id": subscription_id,
                "symbol": symbol,
                "interval": interval,
                "indicator_id": selected_indicator_id,
                "params": normalized_params
            }
            logger.info(f"📊 [{self.agent_id}] Subscribed session {session_id}: {symbol} @ {interval}")
        
        return success
    
    async def send_history_push(
        self,
        session_id: str,
        symbol: str,
        interval: str,
        candles: list[dict[str, Any]]
    ) -> bool:
        """
        Send history_push message to overlay agent with canonical candles.
        
        Used to initialize overlay agents with historical OHLC data.
        """
        if not self.websocket or self.websocket.closed:
            logger.error(f"[{self.agent_id}] Cannot send history_push: not connected")
            return False
        
        subscription = self.subscriptions.get(session_id) or {}
        subscription_id = str(subscription.get("subscription_id") or self._build_subscription_id(session_id))

        transport_limits = (self.metadata or {}).get("transport_limits") or {}
        max_per_chunk = int(
            transport_limits.get("max_records_per_chunk") or DEFAULT_MAX_RECORDS_PER_CHUNK
        )
        max_per_chunk = max(1, max_per_chunk)

        total_candles = len(candles)
        total_chunks = max(1, (total_candles + max_per_chunk - 1) // max_per_chunk)

        logger.info(
            "📚 [%s] Sending history_push for session %s: candles=%s chunks=%s chunk_size=%s",
            self.agent_id,
            session_id,
            total_candles,
            total_chunks,
            max_per_chunk,
        )

        for chunk_index in range(total_chunks):
            start = chunk_index * max_per_chunk
            end = min(start + max_per_chunk, total_candles)
            chunk_candles = candles[start:end]

            message = {
                "type": "history_push",
                "spec_version": ACP_SPEC_VERSION,
                "session_id": session_id,
                "subscription_id": subscription_id,
                "agent_id": self.agent_id,
                "symbol": symbol,
                "interval": interval,
                "candles": chunk_candles,
                "count": len(chunk_candles),
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "is_final_chunk": chunk_index == total_chunks - 1,
            }

            success = await self.send_message(message)
            if not success:
                logger.error(
                    "[%s] Failed sending history_push chunk %s/%s for session %s",
                    self.agent_id,
                    chunk_index + 1,
                    total_chunks,
                    session_id,
                )
                return False

        return True
    
    async def unsubscribe(self, session_id: str) -> bool:
        """
        Unsubscribe from a session.
        
        Args:
            session_id: The session to unsubscribe
        """
        if session_id not in self.subscriptions:
            logger.warning(f"[{self.agent_id}] Session {session_id} not found")
            return False
        
        # ACP v0.4.0: Include required envelope fields in unsubscribe message
        subscription = self.subscriptions.get(session_id) or {}
        subscription_id = str(subscription.get("subscription_id") or self._build_subscription_id(session_id))

        message = {
            "type": "unsubscribe",
            "spec_version": ACP_SPEC_VERSION,
            "session_id": session_id,
            "subscription_id": subscription_id,
            "agent_id": self.agent_id
        }
        
        success = await self.send_message(message)

        # Always clear local subscription state to avoid stale-session reuse,
        # even if the agent connection is unavailable during cleanup.
        self.subscriptions.pop(session_id, None)
        self._subscription_version_by_session.pop(session_id, None)

        if success:
            logger.info(f"🚫 [{self.agent_id}] Unsubscribed session: {session_id}")
        else:
            logger.warning(
                f"[{self.agent_id}] Unsubscribe send failed for {session_id}; "
                "local subscription state was still cleared"
            )

        return success
    
    async def listen(self) -> None:
        """Listen for messages from agent and forward to callback"""
        if not self.websocket:
            logger.error(f"[{self.agent_id}] Cannot listen: not connected")
            return
        
        try:
            while self.running:
                try:
                    message_str = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=30.0  # Timeout to allow checking running flag
                    )
                    
                    try:
                        message = json.loads(message_str)
                        message_type = message.get("type")
                        session_id = message.get("session_id", "unknown")
                        
                        # Suppress noisy overlay_update and heartbeat logs
                        if message_type not in ["overlay_update", "heartbeat"]:
                            logger.info(f"[{self.agent_id}] ⬇️  Received {message_type} message for session {session_id}")
                        
                        # Update agent status based on heartbeat
                        if message_type == "heartbeat":
                            self.agent.status.status = "online"
                            self.agent.status.error_message = None
                        
                        # Log OHLC data for debugging
                        if message_type == "data" and message.get("schema") == "ohlc":
                            record = message.get("record")
                            if isinstance(record, dict):
                                logger.info(
                                    "[%s] 📥 OHLC session=%s id=%s ts=%s state=%s rev=%s",
                                    self.agent_id,
                                    session_id,
                                    record.get("id"),
                                    record.get("ts"),
                                    record.get("bar_state"),
                                    record.get("rev"),
                                )
                        
                        # Forward to callback with session_id
                        if self.on_message:
                            if inspect.iscoroutinefunction(self.on_message):
                                await self.on_message(self.agent_id, session_id, message)
                            else:
                                self.on_message(self.agent_id, session_id, message)
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"[{self.agent_id}] Invalid JSON: {e}")
                        continue
                        
                except asyncio.TimeoutError:
                    # Just a check interval, continue listening
                    continue
                    
                except ConnectionClosed:
                    logger.warning(f"[{self.agent_id}] Connection closed by agent")
                    break
                    
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error in listen loop: {e}")
        
        finally:
            if self.running:
                # Connection lost unexpectedly, try to reconnect
                logger.info(f"[{self.agent_id}] Connection lost, will attempt to reconnect...")
                self.agent.status.status = "offline"
                self.agent.status.error_message = "Connection lost"
                
                # Schedule reconnection
                if not self.reconnect_task or self.reconnect_task.done():
                    self.reconnect_task = asyncio.create_task(self._reconnect())
    
    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff"""
        delay = 3  # Start with 3 seconds
        max_delay = 60
        
        while self.running:
            logger.info(f"[{self.agent_id}] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)
            
            if not self.running:
                break
            
            success = await self.connect()
            if success:
                # Re-establish subscriptions
                for session_id, sub_info in list(self.subscriptions.items()):
                    subscribe_ok = await self.subscribe(
                        session_id,
                        sub_info["symbol"],
                        sub_info["interval"],
                        sub_info["params"],
                        force=True,
                    )
                    if subscribe_ok and self.on_rebootstrap:
                        try:
                            result = self.on_rebootstrap(self.agent_id, session_id)
                            if inspect.isawaitable(result):
                                await result
                        except Exception as exc:
                            logger.error(
                                "[%s] Re-bootstrap failed for session %s: %s",
                                self.agent_id,
                                session_id,
                                exc,
                            )
                
                # Restart listening
                asyncio.create_task(self.listen())
                logger.info(f"✅ [{self.agent_id}] Reconnected successfully")
                break
            else:
                # Exponential backoff
                delay = min(delay * 2, max_delay)
    
    async def start(self) -> bool:
        """Connect and start listening"""
        success = await self.connect()
        if not success:
            return False
        
        # Start listening in background
        asyncio.create_task(self.listen())
        
        return True
    
    async def stop(self) -> None:
        """Stop connection and clean up"""
        # Unsubscribe from all active sessions
        for session_id in list(self.subscriptions.keys()):
            await self.unsubscribe(session_id)
        await self.disconnect()
