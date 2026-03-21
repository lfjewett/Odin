import { useEffect, useState, useCallback, useRef } from "react";
import { MarketCandle, OverlayRecord, OverlaySchema } from "../types/events";
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
  schema: OverlaySchema;
  record: OverlayRecord;
}

export interface OverlayHistoryEvent {
  sessionId: string;
  agentId: string;
  schema: OverlaySchema;
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
  totalBars?: number;
  rangeStartTs?: string | null;
  rangeEndTs?: string | null;
  viewportFromTs?: string | null;
  viewportToTs?: string | null;
  viewportDays?: number;
  isViewported?: boolean;
  isLatest?: boolean;
  followLive?: boolean;
  sliderValue?: number;
}

export interface SubscribeRequest {
  sessionId: string;
  agentId: string;
  symbol: string;
  interval: string;
  timeframeDays: number;
  viewportDays?: number;
}

export interface StateEvent {
  type: "state_event";
  event_name: string;
  domain: "agent" | "overlay" | "trade" | "workspace";
  revision: number;
  session_id?: string;
  emitted_at: string;
  payload?: Record<string, unknown>;
  server_revisions?: {
    agent?: number;
    overlay?: number;
    trade?: number;
    workspace?: number;
  };
}

export interface SyncSnapshot {
  type: "sync_snapshot";
  emitted_at: string;
  stale_domains: Array<"agent" | "overlay" | "trade" | "workspace">;
  server_revisions: {
    agent: number;
    overlay: number;
    trade: number;
    workspace: number;
  };
  trade_sessions?: Array<{
    session_id: string;
    result: Record<string, unknown>;
  }>;
}

/**
 * ACP v0.4.2 WebSocket hook for frontend market data streaming
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
  onStateEvent?: (event: StateEvent) => void,
  onSyncSnapshot?: (snapshot: SyncSnapshot) => void,
  getClientRevisions?: () => { agent: number; overlay: number; trade: number; workspace: number },
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
  const onStateEventRef = useRef(onStateEvent);
  const onSyncSnapshotRef = useRef(onSyncSnapshot);
  const getClientRevisionsRef = useRef(getClientRevisions);
  const clientSyncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
      viewport_days: request.viewportDays,
    };

    ws.send(JSON.stringify(payload));
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

  useEffect(() => {
    onStateEventRef.current = onStateEvent;
  }, [onStateEvent]);

  useEffect(() => {
    onSyncSnapshotRef.current = onSyncSnapshot;
  }, [onSyncSnapshot]);

  useEffect(() => {
    getClientRevisionsRef.current = getClientRevisions;
  }, [getClientRevisions]);

  const sendClientSync = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const revisions = getClientRevisionsRef.current
      ? getClientRevisionsRef.current()
      : { agent: 0, overlay: 0, trade: 0, workspace: 0 };

    ws.send(
      JSON.stringify({
        type: "client_sync",
        revisions,
      })
    );
  }, []);

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
          const messageSessionId = typeof message.session_id === "string" ? message.session_id : null;
          const messageSeq = typeof message.seq === "number" ? message.seq : null;

          // Session stream sequence numbers are shared across all replay-buffered live messages,
          // not just OHLC `data`. Advance tracking on every sequenced session message so overlay
          // updates do not look like false gaps between candles.
          if (
            messageSessionId &&
            messageSeq !== null &&
            (message.type === "data" ||
              message.type === "candle_correction" ||
              message.type === "overlay_update" ||
              message.type === "overlay_marker" ||
              message.type === "history_response")
          ) {
            const lastSeq = lastSeqBySessionRef.current.get(messageSessionId) ?? -1;
            if (messageSeq > lastSeq + 1) {
              console.warn(`[WebSocket] Gap detected for session ${messageSessionId}: expected ${lastSeq + 1}, got ${messageSeq}`);
              sendResyncRequest(messageSessionId, lastSeq);
            }
            lastSeqBySessionRef.current.set(messageSessionId, messageSeq);
          }
          
          // Handle different message types from backend (ACP v0.4.x)
          switch (message.type) {
            case "connection_ready":
              // Store client_id from backend for future use
              setClientId(message.client_id);
              
              // Flush pending subscribe if waiting
              if (pendingSubscribeRef.current) {
                const flushed = sendSubscribePayload(pendingSubscribeRef.current);
                if (flushed) {
                  pendingSubscribeRef.current = null;
                }
              }

              sendClientSync();

              if (clientSyncIntervalRef.current) {
                clearInterval(clientSyncIntervalRef.current);
              }
              clientSyncIntervalRef.current = setInterval(() => {
                sendClientSync();
              }, 10000);
              break;
            
            case "heartbeat":
              if (onStatusUpdateRef.current && message.agent_id && message.status) {
                onStatusUpdateRef.current({
                  agent_id: message.agent_id,
                  status: message.status,
                  timestamp: message.timestamp || new Date().toISOString(),
                });
              }
              break;
            
            case "agent_status_update":
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
              if (onSnapshotRef.current && message.bars) {
                onSnapshotRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  symbol: message.symbol,
                  interval: message.interval,
                  bars: message.bars,
                  totalBars: message.total_bars,
                  rangeStartTs: message.range_start_ts,
                  rangeEndTs: message.range_end_ts,
                  viewportFromTs: message.viewport_from_ts,
                  viewportToTs: message.viewport_to_ts,
                  viewportDays: message.viewport_days,
                  isViewported: message.is_viewported,
                  isLatest: message.is_latest,
                  followLive: message.follow_live,
                  sliderValue: message.slider_value,
                });
                
                // Initialize sequence tracking for this session
                lastSeqBySessionRef.current.set(message.session_id, message.count ? 0 : -1);
              }
              break;
            
            case "data":
              // ACP data message (live updates)
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
              if (onOverlayHistoryRef.current && message.overlays) {
                onOverlayHistoryRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: message.schema as OverlaySchema,
                  overlays: message.overlays,
                });
              }
              break;
            
            case "overlay_update":
              // Live overlay value update
              if (onOverlayRef.current && message.record) {
                onOverlayRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: message.schema as OverlaySchema,
                  record: message.record,
                });
              }
              break;
            
            case "overlay_marker":
              // Event marker from overlay agent
              if (onOverlayRef.current && message.record) {
                onOverlayRef.current({
                  sessionId: message.session_id,
                  agentId: message.agent_id,
                  schema: "event",
                  record: message.record,
                });
              }
              break;

            case "state_event":
              if (onStateEventRef.current) {
                onStateEventRef.current(message as StateEvent);
              }
              break;

            case "sync_snapshot":
              if (onSyncSnapshotRef.current) {
                onSyncSnapshotRef.current(message as SyncSnapshot);
              }
              break;
            
            default:
              break;
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

        if (clientSyncIntervalRef.current) {
          clearInterval(clientSyncIntervalRef.current);
          clientSyncIntervalRef.current = null;
        }

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
    }, [wsUrl, reconnectDelay, sendSubscribePayload, sendClientSync]);

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

      if (clientSyncIntervalRef.current) {
        clearInterval(clientSyncIntervalRef.current);
        clientSyncIntervalRef.current = null;
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