export type ACPSpecVersion = "ACP-0.1.0";

export type ACPMessageType = "data" | "heartbeat" | "error";

export interface ACPOhlcRecord {
  id: string;
  seq?: number;
  rev: number;
  bar_state: "partial" | "final";
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ACPDataMessage {
  type: "data";
  spec_version: ACPSpecVersion;
  subscription_id: string;
  agent_id: string;
  schema: "ohlc" | "line" | "event" | "band" | "histogram" | "forecast";
  record: Record<string, unknown>;
}

export interface ACPHeartbeatMessage {
  type: "heartbeat";
  spec_version: ACPSpecVersion;
  subscription_id: string;
  agent_id: string;
  status: "ok" | "degraded" | "error";
  uptime_s: number;
  last_event_ts: string;
}

export interface ACPErrorMessage {
  type: "error";
  spec_version: ACPSpecVersion;
  subscription_id: string;
  agent_id: string;
  code:
    | "INVALID_REQUEST"
    | "INVALID_SYMBOL"
    | "INVALID_INTERVAL"
    | "INVALID_PARAMS"
    | "UNSUPPORTED_OPERATION"
    | "SUBSCRIPTION_NOT_FOUND"
    | "AGENT_OVERLOADED"
    | "BACKFILL_TIMEOUT"
    | "INTERNAL_ERROR";
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export type ACPMessageEnvelope = ACPDataMessage | ACPHeartbeatMessage | ACPErrorMessage;

export type MarketCandle = ACPOhlcRecord;
