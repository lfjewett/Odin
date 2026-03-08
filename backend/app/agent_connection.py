"""
Agent WebSocket Connection Manager (ACP v0.2.0)

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


class AgentConnection:
    """
    Manages WebSocket connection and subscriptions for a single agent.
    
    ACP v0.2.0: Supports multiple concurrent sessions per agent.
    Each session is isolated (session_id in all messages).
    """
    
    def __init__(
        self,
        agent: Agent,
        on_message: Callable[[str, str, dict[str, Any]], None] | None = None
    ):
        """
        Args:
            agent: Agent configuration
            on_message: Callback(agent_id, session_id, message) for incoming messages
        """
        self.agent = agent
        self.on_message = on_message
        self.websocket: WebSocketClientProtocol | None = None
        
        # Subscriptions: session_id -> subscription_info
        # Format: {
        #   "session_id": "...",
        #   "symbol": "SPY",
        #   "interval": "1m",
        #   "params": {...}
        # }
        self.subscriptions: dict[str, dict[str, Any]] = {}
        
        self.running = False
        self.reconnect_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        
    @property
    def agent_id(self) -> str:
        return self.agent.agent_id
    
    @property
    def ws_url(self) -> str:
        """Build WebSocket URL from agent's base URL"""
        base_url = self.agent.config.agent_url
        # Convert http:// to ws://
        ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_base}/ws/live"
    
    @property
    def http_base_url(self) -> str:
        """Get HTTP base URL for REST API calls"""
        return self.agent.config.agent_url

    @staticmethod
    def _format_history_timestamp(value: datetime) -> str:
        """Format timestamps for agent /history queries as UTC ISO-8601 with Z suffix."""
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    
    async def connect(self) -> bool:
        """Establish WebSocket connection to agent"""
        if self.websocket and not self.websocket.closed:
            logger.warning(f"[{self.agent_id}] Already connected")
            return True
        
        try:
            logger.info(f"[{self.agent_id}] Connecting to {self.ws_url}...")
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10
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
        params: dict[str, Any] | None = None
    ) -> bool:
        """
        Subscribe to live data stream for a session.
        
        Args:
            session_id: Unique session identifier (client ID + chart view)
            symbol: Trade symbol (e.g., "SPY")
            interval: Candle interval (e.g., "1m")
            params: Optional parameters like timeframe_days
        """
        if not self.websocket or self.websocket.closed:
            logger.error(f"[{self.agent_id}] Cannot subscribe: not connected")
            return False

        normalized_params = params or {}
        existing = self.subscriptions.get(session_id)
        if existing:
            existing_timeframe = int((existing.get("params") or {}).get("timeframe_days", 7))
            requested_timeframe = int(normalized_params.get("timeframe_days", 7))
            if (
                existing.get("symbol") == symbol
                and existing.get("interval") == interval
                and existing_timeframe == requested_timeframe
            ):
                logger.info(
                    f"[{self.agent_id}] Subscribe request unchanged for {session_id}, skipping"
                )
                return True
        
        # ACP v0.2.0: Include session_id in subscribe message
        message = {
            "type": "subscribe",
            "spec_version": self.agent.config.spec_version,
            "session_id": session_id,
            "agent_id": self.agent_id,
            "symbol": symbol,
            "interval": interval,
            "params": normalized_params
        }
        
        success = await self.send_message(message)
        if success:
            self.subscriptions[session_id] = {
                "session_id": session_id,
                "symbol": symbol,
                "interval": interval,
                "params": normalized_params
            }
            logger.info(f"📊 [{self.agent_id}] Subscribed session {session_id}: {symbol} @ {interval}")
        
        return success
    
    async def unsubscribe(self, session_id: str) -> bool:
        """
        Unsubscribe from a session.
        
        Args:
            session_id: The session to unsubscribe
        """
        if session_id not in self.subscriptions:
            logger.warning(f"[{self.agent_id}] Session {session_id} not found")
            return False
        
        # ACP v0.2.0: Include session_id in unsubscribe message
        message = {
            "type": "unsubscribe",
            "spec_version": self.agent.config.spec_version,
            "session_id": session_id
        }
        
        success = await self.send_message(message)
        if success:
            del self.subscriptions[session_id]
            logger.info(f"🚫 [{self.agent_id}] Unsubscribed session: {session_id}")
        
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
                    await self.subscribe(
                        session_id,
                        sub_info["symbol"],
                        sub_info["interval"],
                        sub_info["params"]
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
