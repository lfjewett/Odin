import { useEffect, useState, useCallback, useRef } from "react";
import { MarketCandle } from "../types/events";

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

export function useEventStream(
  onEvent: (event: CandleEvent) => void,
  onStatusUpdate?: (update: StatusUpdate) => void,
  options: UseEventStreamOptions = {}
) {
  const {
    wsUrl = "ws://localhost:8001/ws",
    reconnectDelay = 3000,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onEventRef = useRef(onEvent);
  const onStatusUpdateRef = useRef(onStatusUpdate);
  const mockTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    onStatusUpdateRef.current = onStatusUpdate;
  }, [onStatusUpdate]);

  const startMockData = useCallback(() => {
    // Generate mock candle data until we have real agent streams
    let currentTs = Date.now();
    let price = 145;
    let rev = 0;

    mockTimerRef.current = setInterval(() => {
      currentTs += 60_000;
      rev += 1;

      const open = price;
      const close = Math.max(1, open + Math.sin(rev / 5) * 0.8 + Math.cos(rev / 7) * 0.3);
      const high = Math.max(open, close) + 0.4;
      const low = Math.min(open, close) - 0.4;

      price = close;

      onEventRef.current({
        subscriptionId: "price_agent",
        agentId: "price_agent",
        candle: {
          id: `live-${currentTs}`,
          ts: new Date(currentTs).toISOString(),
          open: Number(open.toFixed(2)),
          high: Number(high.toFixed(2)),
          low: Number(low.toFixed(2)),
          close: Number(close.toFixed(2)),
          volume: 10_000 + (rev % 2000),
          rev,
          bar_state: "partial",
          seq: rev,
        },
      });
    }, 2_000);
  }, []);

  const stopMockData = useCallback(() => {
    if (mockTimerRef.current) {
      clearInterval(mockTimerRef.current);
      mockTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    console.log(`[WebSocket] Connecting to ${wsUrl}...`);
    
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WebSocket] Connected successfully");
        setIsConnected(true);
        
        // Start mock data generation (until we have real agents)
        startMockData();
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
        stopMockData();
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
  }, [wsUrl, reconnectDelay, startMockData, stopMockData]);

  useEffect(() => {
    connect();

    return () => {
      console.log("[WebSocket] Cleaning up connection...");
      
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      
      stopMockData();
      
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect, stopMockData]);

  return { isConnected };
}
