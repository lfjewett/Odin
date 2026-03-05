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
  fromTs: string,
  toTs: string,
  interval: string
): Promise<HistoryResponse> {
  // TODO(new-project): replace mock history generation with backend GET /api/history integration.
  console.info(
    "TODO(new-project): fetch history from backend /api/history instead of generated mock candles."
  );

  const fromMs = Date.parse(fromTs);
  const toMs = Date.parse(toTs);

  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
    throw new Error("Invalid history timeframe");
  }

  const intervalMap: Record<string, number> = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
  };
  const stepMs = intervalMap[interval] ?? 60_000;

  const data: OHLCBar[] = [];
  const seed = symbol
    .split("")
    .reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  let previousClose = 100 + (seed % 50);
  let index = 0;

  for (let current = fromMs; current < toMs; current += stepMs) {
    const swing = Math.sin(index / 8) * 0.9 + Math.cos(index / 5) * 0.5;
    const drift = (seed % 7) * 0.02;

    const open = previousClose;
    const close = Math.max(1, open + swing + drift);
    const high = Math.max(open, close) + Math.abs(Math.sin(index / 3)) * 0.6;
    const low = Math.min(open, close) - Math.abs(Math.cos(index / 4)) * 0.6;

    data.push({
      id: `${symbol}-${interval}-${current}`,
      seq: index,
      rev: 0,
      bar_state: "final",
      ts: new Date(current).toISOString(),
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.round(20_000 + Math.abs(Math.sin(index / 2)) * 80_000),
    });

    previousClose = close;
    index += 1;
  }

  return {
    spec_version: "ACP-0.1.0",
    agent_id: "ui_only_mock_price_agent",
    schema: "ohlc",
    symbol,
    interval,
    data,
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
