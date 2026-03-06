import { useEffect, useState, useCallback, useRef } from "react";
import { MarketCandle } from "../types/events";
import type { OHLCBar } from "../history/fetchHistory";

interface UseEventStreamOptions {
  wsUrl?: string;
  reconnectDelay?: number;
}

export interface CandleEvent {
  candle: MarketCandle;
  subscriptionId: string;
  agentId: string;
}

export interface StatusUpdate {
  agent_id: string;
  status: "online" | "offline" | "error" | "connecting";
  error_message?: string | null;
  timestamp: string;
}

export interface SnapshotEvent {
  agentId: string;
  subscriptionId: string;
  symbol: string;
  interval: string;
  bars: OHLCBar[];
}

export interface SubscribeRequest {
  agentId: string;
  symbol: string;
  interval: string;
  timeframeDays: number;
}

export function useEventStream(
  onEvent: (event: CandleEvent) => void,
  onStatusUpdate?: (update: StatusUpdate) => void,
  onSnapshot?: (event: SnapshotEvent) => void,
  options: UseEventStreamOptions = {}
) {
  const {
    wsUrl = "ws://localhost:8001/ws",
    reconnectDelay = 3000,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSubscribeRef = useRef<SubscribeRequest | null>(null);
  const onEventRef = useRef(onEvent);
  const onStatusUpdateRef = useRef(onStatusUpdate);
  const onSnapshotRef = useRef(onSnapshot);

  const sendSubscribePayload = useCallback((request: SubscribeRequest): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return false;
    }

    const payload = {
      type: "subscribe_request",
      agent_id: request.agentId,
      symbol: request.symbol,
      interval: request.interval,
      timeframe_days: request.timeframeDays,
    };

    ws.send(JSON.stringify(payload));
    console.log("[WebSocket] Sent subscribe_request:", payload);
    return true;
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

  const connect = useCallback(() => {
    console.log(`[WebSocket] Connecting to ${wsUrl}...`);
    
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WebSocket] Connected successfully");
        setIsConnected(true);

        if (pendingSubscribeRef.current) {
          const flushed = sendSubscribePayload(pendingSubscribeRef.current);
          if (flushed) {
            console.log("[WebSocket] Flushed pending subscribe_request on connect");
          }
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          console.log("[WebSocket] Received message:", message);
          
          // Handle different message types from backend
          switch (message.type) {
            case "connection":
              console.log("[WebSocket] Connection confirmed:", message.message);
              break;
            
            case "heartbeat":
              console.log("[WebSocket] Heartbeat received");
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
            
            case "data":
              // ACP data message
              console.log("[WebSocket] ACP data message:", message.agent_id, message.subscription_id);
              if (message.schema === "ohlc" && message.record) {
                // Forward OHLC candle to callback
                onEventRef.current({
                  subscriptionId: message.subscription_id,
                  agentId: message.agent_id,
                  candle: message.record as MarketCandle,
                });
              }
              break;
            
            case "snapshot":
              // Historical data snapshot
              console.log("[WebSocket] Snapshot received:", message.agent_id, message.count || 0, "bars");
              if (onSnapshotRef.current && message.bars) {
                onSnapshotRef.current({
                  agentId: message.agent_id,
                  subscriptionId: message.subscription_id,
                  symbol: message.symbol,
                  interval: message.interval,
                  bars: message.bars,
                });
              }
              break;
            
            case "error":
              // ACP error message
              console.error("[WebSocket] ACP error:", message.code, message.message);
              if (onStatusUpdateRef.current) {
                onStatusUpdateRef.current({
                  agent_id: message.agent_id,
                  status: "error",
                  error_message: `${message.code}: ${message.message}`,
                  timestamp: new Date().toISOString(),
                });
              }
              break;
            
            default:
              console.log("[WebSocket] Unknown message type:", message.type);
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
    }, [wsUrl, reconnectDelay]);

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
    pendingSubscribeRef.current = request;
    return sendSubscribePayload(request);
  }, [sendSubscribePayload]);

  return { isConnected, sendSubscribeRequest };
}