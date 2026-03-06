/**
 * Fetch historical OHLC data from the backend.
 * 
 * The backend proxies to the configured price agent's /history endpoint.
 * 
 * @param symbol Trading symbol (e.g., "SPY")
 * @param fromTs Start timestamp (ISO-8601 format, inclusive)
 * @param toTs End timestamp (ISO-8601 format, exclusive)
 * @param interval Candle interval (e.g., "1m", "5m", "1h")
 * @returns ACP history response with finalized OHLC bars
 */
export interface OHLCBar {
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

export interface HistoryResponse {
  spec_version: string;
  agent_id: string;
  schema: string;
  symbol: string;
  interval: string;
  data: OHLCBar[];
}

export async function fetchHistory(
  symbol: string,
  _fromTs: string,
  _toTs: string,
  interval: string
): Promise<HistoryResponse> {
  // Return empty dataset; historical data will come from backend API in next phase
  // For now, chart starts clean and receives live data only
  console.info(
    "[fetchHistory] Returning empty dataset. Chart will load with live data from price agent."
  );

  return {
    spec_version: "ACP-0.1.0",
    agent_id: "price_agent",
    schema: "ohlc",
    symbol,
    interval,
    data: [],
  };
}

/**
 * Calculate start and end timestamps for a given timeframe.
 * 
 * @param days Number of days of history to fetch
 * @returns Object with fromTs and toTs in ISO-8601 format
 */
export function getTimeframeTimestamps(days: number): { fromTs: string; toTs: string } {
  const now = new Date();
  const to = now.toISOString();
  
  const from = new Date(now);
  from.setDate(from.getDate() - days);
  
  return {
    fromTs: from.toISOString(),
    toTs: to,
  };
}
