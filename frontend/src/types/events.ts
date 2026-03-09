/**
 * ACP v0.3.0 Protocol Types
 * 
 * Defines TypeScript types for ACP (Agent Communication Protocol) v0.3.0
 * with session isolation, sequence tracking, and bidirectional communication.
 */

export type ACPSpecVersion = "ACP-0.3.0";

export type BarState = "partial" | "provisional_close" | "session_reconciled" | "final";

// Message types from agents and backend
export type ACPMessageType =
  | "connection_ready"
  | "subscribe_request"
  | "unsubscribe_request"
  | "snapshot"
  | "data"
  | "heartbeat"
  | "candle_correction"
  | "resync_request"
  | "resync_response"
  | "history_response"
  | "overlay_update"
  | "overlay_marker"
  | "error"
  | "agent_status_update";

/**
 * ACP OHLC Record (Candlestick)
 * 
 * Core data structure for market data with:
 * - Session-specific deduplication (agent_id, id, rev)
 * - Bar state lifecycle tracking
 * - Monotonic revision counter
 */
export interface ACPOhlcRecord {
  id: string; // Unique candle identifier
  seq?: number; // Sequence number for message ordering
  rev: number; // Revision counter (monotonic per candle id)
  bar_state: BarState; // Current state in candle lifecycle
  ts: string; // ISO-8601 timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * Connection Ready (Backend -> Frontend)
 * 
 * Sent immediately on WebSocket connection establishment.
 * Provides client_id to use in all subsequent messages.
 */
export interface ConnectionReadyMessage {
  type: "connection_ready";
  client_id: string;
  acp_version: ACPSpecVersion;
  timestamp: string;
  message: string;
}

/**
 * Subscribe Request (Frontend -> Backend)
 * 
 * Requests subscription to a data stream for a specific session.
 */
export interface SubscribeRequestMessage {
  type: "subscribe_request";
  session_id: string; // Unique session identifier (from frontend)
  agent_id: string; // Which agent to subscribe to
  symbol: string; // Trade symbol (e.g., "SPY")
  interval: string; // Candle interval (e.g., "1m")
  timeframe_days?: number; // Historical backfill duration
}

/**
 * Unsubscribe Request (Frontend -> Backend)
 * 
 * Requests termination of a session subscription.
 */
export interface UnsubscribeRequestMessage {
  type: "unsubscribe_request";
  session_id: string;
}

/**
 * Snapshot (Backend -> Frontend)
 * 
 * Historical data snapshot after subscription (finalized bars only).
 */
export interface SnapshotMessage {
  type: "snapshot";
  session_id: string;
  agent_id: string;
  symbol: string;
  interval: string;
  bars: ACPOhlcRecord[];
  count: number;
  acp_version: ACPSpecVersion;
  timestamp: string;
}

/**
 * Data Message (Backend -> Frontend or Agent -> Backend)
 * 
 * Live market data update (OHLC, event, line, etc.).
 */
export interface ACPDataMessage {
  type: "data";
  spec_version: ACPSpecVersion;
  session_id: string; // ACP v0.3.0 routing key
  agent_id: string;
  schema: "ohlc" | "line" | "event" | "band" | "histogram" | "forecast";
  record: Record<string, unknown>;
  seq?: number; // For gap detection
}

/**
 * Heartbeat Message (Backend -> Frontend)
 * 
 * Periodic health check to keep connection alive and signal agent status.
 */
export interface ACPHeartbeatMessage {
  type: "heartbeat";
  session_id?: string; // Optional for global heartbeats
  agent_id?: string; // Optional for global heartbeats
  acp_version: ACPSpecVersion;
  timestamp: string;
  status?: "ok" | "degraded" | "error";
}

/**
 * Candle Correction (Backend -> Frontend)
 * 
 * Notifies of a candle update with higher revision number.
 * Frontend should upsert this over the existing candle.
 */
export interface CandleCorrectionMessage {
  type: "candle_correction";
  spec_version: ACPSpecVersion;
  session_id: string;
  agent_id: string;
  record: ACPOhlcRecord; // Updated/corrected OHLC record
  seq?: number;
}

/**
 * Resync Request (Frontend -> Backend)
 * 
 * Sent by frontend when gap detected in message sequence.
 * Requests replay of missed messages from backend buffer.
 */
export interface ResyncRequestMessage {
  type: "resync_request";
  session_id: string;
  last_seq_received: number; // Last sequence number received by frontend
}

/**
 * Resync Response (Backend -> Frontend)
 * 
 * Contains buffered messages since the requested sequence number.
 */
export interface ResyncResponseMessage {
  type: "resync_response";
  session_id: string;
  messages: Array<Record<string, unknown>>; // Messages with seq > last_seq_received
  count: number;
  timestamp: string;
}

/**
 * Error Message (Backend -> Frontend or Agent -> Backend)
 * 
 * Error notification with optional recovery instructions.
 */
export interface ACPErrorMessage {
  type: "error";
  spec_version?: ACPSpecVersion;
  session_id?: string;
  agent_id?: string;
  code:
    | "INVALID_REQUEST"
    | "INVALID_SYMBOL"
    | "INVALID_INTERVAL"
    | "INVALID_PARAMS"
    | "UNSUPPORTED_OPERATION"
    | "SESSION_NOT_FOUND"
    | "SUBSCRIPTION_NOT_FOUND"
    | "AGENT_OVERLOADED"
    | "BACKFILL_TIMEOUT"
    | "INTERNAL_ERROR";
  message: string;
  retryable?: boolean;
  details?: Record<string, unknown>;
}

/**
 * Agent Status Update (Backend -> Frontend)
 * 
 * Notifies of agent connection status change.
 */
export interface AgentStatusUpdateMessage {
  type: "agent_status_update";
  agent_id: string;
  status: "online" | "offline" | "error";
  error_message?: string | null;
  timestamp: string;
}

/**
 * Generic Overlay Record
 * 
 * Base structure for overlay data (line, event, band, histogram, forecast).
 */
export interface OverlayRecord {
  id: string; // Unique record identifier
  ts: string; // ISO-8601 timestamp
  [key: string]: unknown; // Additional fields depend on schema
}

/**
 * Line Overlay Record (e.g., EMA, SMA)
 */
export interface LineRecord extends OverlayRecord {
  value: number;
  label?: string;
}

/**
 * Event Overlay Record (markers)
 */
export interface EventRecord extends OverlayRecord {
  title: string;
  description?: string;
  severity?: "info" | "warning" | "critical";
}

/**
 * History Response (Backend -> Frontend)
 * 
 * Overlay agent's response to history_push with computed overlay values.
 */
export interface HistoryResponseMessage {
  type: "history_response";
  spec_version: ACPSpecVersion;
  session_id: string;
  subscription_id: string;
  agent_id: string;
  schema: "line" | "event" | "band" | "histogram" | "forecast";
  overlays: OverlayRecord[];
  metadata?: Record<string, unknown>;
}

/**
 * Overlay Update (Backend -> Frontend)
 * 
 * Live overlay value update from overlay agent.
 */
export interface OverlayUpdateMessage {
  type: "overlay_update";
  spec_version: ACPSpecVersion;
  session_id: string;
  subscription_id: string;
  agent_id: string;
  schema: "line" | "event" | "band" | "histogram" | "forecast";
  record: OverlayRecord;
}

/**
 * Overlay Marker (Backend -> Frontend)
 * 
 * Event marker from overlay agent.
 */
export interface OverlayMarkerMessage {
  type: "overlay_marker";
  spec_version: ACPSpecVersion;
  session_id: string;
  subscription_id: string;
  agent_id: string;
  schema: "event";
  record: EventRecord;
}

/**
 * Union type for all ACP v0.3.0 message types
 */
export type ACPMessageEnvelope =
  | ConnectionReadyMessage
  | SubscribeRequestMessage
  | UnsubscribeRequestMessage
  | SnapshotMessage
  | ACPDataMessage
  | ACPHeartbeatMessage
  | CandleCorrectionMessage
  | ResyncRequestMessage
  | ResyncResponseMessage
  | ACPErrorMessage
  | AgentStatusUpdateMessage
  | HistoryResponseMessage
  | OverlayUpdateMessage
  | OverlayMarkerMessage;

export type MarketCandle = ACPOhlcRecord;
