import { useEffect, useState, useCallback, useRef } from "react";
import { MarketCandle, OverlayRecord } from "../types/events";
import type { OHLCBar } from "../history/fetchHistory";

interface UseEventStreamOptions {
  wsUrl?: string;
  reconnectDelay?: number;
}

export interface CandleEvent {
  candle: MarketCandle;
  sessionId: string;
  agentId: string;
}

export interface OverlayEvent {
  sessionId: string;
  agentId: string;
  schema: "line" | "event" | "band" | "histogram" | "forecast";
  record: OverlayRecord;
}

export interface OverlayHistoryEvent {
  sessionId: string;
  agentId: string;
  schema: "line" | "event" | "band" | "histogram" | "forecast";
  overlays: OverlayRecord[];
}

export interface StatusUpdate {
  agent_id: string;
  status: "online" | "offline" | "error" | "connecting";
  error_message?: string | null;
  timestamp: string;
}

export interface SnapshotEvent {
  sessionId: string;
  agentId: string;
  symbol: string;
  interval: string;
  bars: OHLCBar[];
}

export interface SubscribeRequest {
  sessionId: string;
  agentId: string;
  symbol: string;
  interval: string;
  timeframeDays: number;
}

/**
 * ACP v0.3.0 WebSocket hook for frontend market data streaming
 * 
 * Manages:
 * - Session-aware subscriptions (session_id per chart/view)
 * - Sequence tracking for gap detection
 * - Automatic resync requests on message loss
 * - Bidirectional communication with backend
 * - Overlay data (line, event, etc.) from indicator agents
 */
export function useEventStream(
  onEvent: (event: CandleEvent) => void,
  onStatusUpdate?: (update: StatusUpdate) => void,
  onSnapshot?: (event: SnapshotEvent) => void,
  onOverlay?: (event: OverlayEvent) => void,
  onOverlayHistory?: (event: OverlayHistoryEvent) => void,
  options: UseEventStreamOptions = {}
) {
  const {
    wsUrl = (() => {
      // Use relative WebSocket path that works in both dev (via Vite proxy) and production
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host; // includes port
      return `${protocol}//${host}/ws`;
    })(),
    reconnectDelay = 3000,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [clientId, setClientId] = useState<string | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSubscribeRef = useRef<SubscribeRequest | null>(null);
  
  // Sequence tracking for gap detection
  const lastSeqBySessionRef = useRef<Map<string, number>>(new Map()); // sessionId -> last_seq
  
  const onEventRef = useRef(onEvent);
  const onStatusUpdateRef = useRef(onStatusUpdate);
  const onSnapshotRef = useRef(onSnapshot);
  const onOverlayRef = useRef(onOverlay);
  const onOverlayHistoryRef = useRef(onOverlayHistory);

  const sendSubscribePayload = useCallback((request: SubscribeRequest): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("[WebSocket] Cannot subscribe: not connected");
      return false;
    }

    const payload = {
      type: "subscribe_request",
      session_id: request.sessionId,
      agent_id: request.agentId,
      symbol: request.symbol,
      interval: request.interval,
      timeframe_days: request.timeframeDays,
    };

    ws.send(JSON.stringify(payload));
    console.log("[WebSocket] Sent subscribe_request:", payload);
    return true;
  }, []);

  const sendResyncRequest = useCallback((sessionId: string, lastSeq: number): void => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("[WebSocket] Cannot send resync: not connected");
      return;
    }

    const payload = {
      type: "resync_request",
      session_id: sessionId,
      last_seq_received: lastSeq,
    };

    ws.send(JSON.stringify(payload));
    console.log("[WebSocket] Sent resync_request for session", sessionId, "since seq", lastSeq);
  }, []);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    onStatusUpdateRef.current = onStatusUpdate;
  }, [onStatusUpdate]);
  
  useEffect(() => {
    onSnapshotRef.current = onSnapshot;
  }, [onSnapshot]);
  
  useEffect(() => {
    onOverlayRef.current = onOverlay;
  }, [onOverlay]);
  
  useEffect(() => {
    onOverlayHistoryRef.current = onOverlayHistory;
  }, [onOverlayHistory]);

  const connect = useCallback(() => {
    console.log(`[WebSocket] Connecting to ${wsUrl}...`);
    
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WebSocket] Connected successfully");
        setIsConnected(true);
        // clientId will be set when connection_ready is received
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          console.log("[WebSocket] Received message:", message.type);
          
          // Handle different message types from backend (ACP v0.3.0)
          switch (message.type) {
            case "connection_ready":
              // Store client_id from backend for future use
              console.log("[WebSocket] Connection ready, client_id:", message.client_id);
              setClientId(message.client_id);
              
              // Flush pending subscribe if waiting
              if (pendingSubscribeRef.current) {
                const flushed = sendSubscribePayload(pendingSubscribeRef.current);
                if (flushed) {
                  console.log("[WebSocket] Flushed pending subscribe_request on connect");
                  pendingSubscribeRef.current = null;
                }
              }
              break;
            
            case "heartbeat":
              console.log("[WebSocket] Heartbeat received");
              if (onStatusUpdateRef.current && message.agent_id && message.status) {
                onStatusUpdateRef.current({
                  agent_id: message.agent_id,
                  status: message.status,
                  timestamp: message.timestamp || new Date().toISOString(),
                });
              }
              break;
            
            case "agent_status_update":
              console.log("[WebSocket] Agent status update:", message.agent_id, message.status);
              if (onStatusUpdateRef.current) {
                onStatusUpdateRef.current({
                  agent_id: message.agent_id,
                  status: message.status,
                  error_message: message.error_message,
                  timestamp: message.timestamp,
                });
              }
              break;
            
            case "snapshot":
              // Historical data snapshot (all canonical bars including in-flight states)
              console.log("[WebSocket] Snapshot received:", message.session_id, message.count || 0, "bars");
              if (onSnapshotRef.current && message.bars) {
                onSnapshotRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  symbol: message.symbol,
                  interval: message.interval,
                  bars: message.bars,
                });
                
                // Initialize sequence tracking for this session
                lastSeqBySessionRef.current.set(message.session_id, message.count ? 0 : -1);
              }
              break;
            
            case "data":
              // ACP data message (live updates)
              console.log("[WebSocket] Data message:", message.session_id, message.schema);
              
              // Track sequence for gap detection
              const sessionId = message.session_id;
              if (message.seq !== undefined) {
                const lastSeq = lastSeqBySessionRef.current.get(sessionId) ?? -1;
                if (message.seq > lastSeq + 1) {
                  // Gap detected!
                  console.warn(`[WebSocket] Gap detected for session ${sessionId}: expected ${lastSeq + 1}, got ${message.seq}`);
                  // Request resync from backend
                  sendResyncRequest(sessionId, lastSeq);
                }
                lastSeqBySessionRef.current.set(sessionId, message.seq);
              }
              
              if (message.schema === "ohlc" && message.record) {
                // Forward OHLC candle to callback
                onEventRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  candle: message.record as MarketCandle,
                });
              }
              break;
            
            case "candle_correction":
              // Candle update with higher revision (upsert)
              console.log("[WebSocket] Candle correction:", message.session_id, message.record?.id);
              if (message.record) {
                onEventRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  candle: message.record as MarketCandle,
                });
              }
              break;
            
            case "resync_response":
              // Replay buffer from backend after gap
              console.log("[WebSocket] Resync response with", message.count, "messages");
              if (message.messages && Array.isArray(message.messages)) {
                for (const msg of message.messages) {
                  // Re-process each message as if fresh
                  if (msg.type === "data" && msg.schema === "ohlc" && msg.record) {
                    onEventRef.current({
                      sessionId: msg.session_id,
                      agentId: msg.agent_id,
                      candle: msg.record as MarketCandle,
                    });
                  } else if (msg.type === "candle_correction" && msg.record) {
                    onEventRef.current({
                      sessionId: msg.session_id,
                      agentId: msg.agent_id,
                      candle: msg.record as MarketCandle,
                    });
                  }
                  // Update sequence tracking
                  if (msg.seq !== undefined) {
                    lastSeqBySessionRef.current.set(msg.session_id, msg.seq);
                  }
                }
              }
              break;
            
            case "error":
              // ACP error message
              console.error("[WebSocket] ACP error:", message.code, message.message);
              if (onStatusUpdateRef.current) {
                onStatusUpdateRef.current({
                  agent_id: message.agent_id || "unknown",
                  status: "error",
                  error_message: `${message.code}: ${message.message}`,
                  timestamp: new Date().toISOString(),
                });
              }
              break;
            
            case "history_response":
              // Overlay agent's response with computed overlay values
              console.log("[WebSocket] History response:", message.session_id, message.overlays?.length || 0, "overlays");
              if (onOverlayHistoryRef.current && message.overlays) {
                onOverlayHistoryRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: message.schema as "line" | "event" | "band" | "histogram" | "forecast",
                  overlays: message.overlays,
                });
              }
              break;
            
            case "overlay_update":
              // Live overlay value update
              console.log("[WebSocket] Overlay update:", message.session_id, message.schema);
              if (onOverlayRef.current && message.record) {
                onOverlayRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: message.schema as "line" | "event" | "band" | "histogram" | "forecast",
                  record: message.record,
                });
              }
              break;
            
            case "overlay_marker":
              // Event marker from overlay agent
              console.log("[WebSocket] Overlay marker:", message.session_id);
              if (onOverlayRef.current && message.record) {
                onOverlayRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: "event",
                  record: message.record,
                });
              }
              break;
            
            default:
              console.log("[WebSocket] Received message type:", message.type);
          }
        } catch (err) {
          console.error("[WebSocket] Failed to parse message:", err);
        }
      };

      ws.onerror = (event) => {
        console.error("[WebSocket] Error:", event);
      };

      ws.onclose = (event) => {
        console.log(`[WebSocket] Disconnected (code: ${event.code}, reason: ${event.reason})`);
        setIsConnected(false);
        setClientId(null);
        wsRef.current = null;

        // Attempt to reconnect after delay
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
        }
        
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log("[WebSocket] Attempting to reconnect...");
          connect();
        }, reconnectDelay);
      };
    } catch (err) {
      console.error("[WebSocket] Failed to create connection:", err);
      setIsConnected(false);
    }
    }, [wsUrl, reconnectDelay, sendSubscribePayload]);

  useEffect(() => {
    connect();

    return () => {
      console.log("[WebSocket] Cleaning up connection...");
      
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  const sendSubscribeRequest = useCallback((request: SubscribeRequest): boolean => {
    if (!isConnected) {
      console.log("[WebSocket] Not connected yet, queuing subscribe_request");
      pendingSubscribeRef.current = request;
      return false;
    }
    
    // Reset sequence tracking for new session
    lastSeqBySessionRef.current.set(request.sessionId, -1);
    
    return sendSubscribePayload(request);
  }, [isConnected, sendSubscribePayload]);

  return { isConnected, clientId, sendSubscribeRequest };
}