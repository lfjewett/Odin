import React, { useMemo } from 'react';
import type { TradeMarker, TradePerformance } from '../workspace/workspaceApi';
import type { OHLCBar } from '../history/fetchHistory';
import './TradesModal.css';

interface TradesModalProps {
  strategyName: string | null;
  markers: TradeMarker[];
  candles: Map<string, OHLCBar>;
  performance: TradePerformance | null;
  onClose: () => void;
}

interface ComputedTrade {
  n: number;
  side: 'Long' | 'Short';
  openTs: string;
  closeTs: string;
  durationMs: number;
  entryPrice: number | null;
  exitPrice: number | null;
  shares: number;
  grossPl: number | null;
  isWin: boolean | null;
  portfolioValue: number;
}

const STARTING_CAPITAL = 10_000;

const computeTrades = (
  markers: TradeMarker[],
  candles: Map<string, OHLCBar>,
): ComputedTrade[] => {
  const sorted = [...markers].sort(
    (a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime(),
  );

  const trades: ComputedTrade[] = [];
  let cash = STARTING_CAPITAL;
  let longEntry: { ts: string; candle_id?: string } | null = null;
  let shortEntry: { ts: string; candle_id?: string } | null = null;

  for (const m of sorted) {
    const action = m.action;

    if (action === 'LONG_ENTRY') {
      longEntry = { ts: m.ts, candle_id: m.candle_id };
    } else if (action === 'LONG_EXIT' && longEntry) {
      const entryPrice = longEntry.candle_id ? (candles.get(longEntry.candle_id)?.close ?? null) : null;
      const exitPrice = m.candle_id ? (candles.get(m.candle_id)?.close ?? null) : null;
      const shares = entryPrice && entryPrice > 0 ? Math.max(1, Math.floor(cash / entryPrice)) : 0;
      const grossPl =
        entryPrice !== null && exitPrice !== null ? shares * (exitPrice - entryPrice) : null;
      if (grossPl !== null) cash += grossPl;

      trades.push({
        n: trades.length + 1,
        side: 'Long',
        openTs: longEntry.ts,
        closeTs: m.ts,
        durationMs: new Date(m.ts).getTime() - new Date(longEntry.ts).getTime(),
        entryPrice,
        exitPrice,
        shares,
        grossPl,
        isWin: grossPl !== null ? grossPl > 0 : null,
        portfolioValue: cash,
      });
      longEntry = null;
    } else if (action === 'SHORT_ENTRY') {
      shortEntry = { ts: m.ts, candle_id: m.candle_id };
    } else if (action === 'SHORT_EXIT' && shortEntry) {
      const entryPrice = shortEntry.candle_id ? (candles.get(shortEntry.candle_id)?.close ?? null) : null;
      const exitPrice = m.candle_id ? (candles.get(m.candle_id)?.close ?? null) : null;
      const shares = entryPrice && entryPrice > 0 ? Math.max(1, Math.floor(cash / entryPrice)) : 0;
      // Short P/L: profit when price goes down
      const grossPl =
        entryPrice !== null && exitPrice !== null ? shares * (entryPrice - exitPrice) : null;
      if (grossPl !== null) cash += grossPl;

      trades.push({
        n: trades.length + 1,
        side: 'Short',
        openTs: shortEntry.ts,
        closeTs: m.ts,
        durationMs: new Date(m.ts).getTime() - new Date(shortEntry.ts).getTime(),
        entryPrice,
        exitPrice,
        shares,
        grossPl,
        isWin: grossPl !== null ? grossPl > 0 : null,
        portfolioValue: cash,
      });
      shortEntry = null;
    }
  }

  return trades;
};

const fmtDollars = (v: number | null): string => {
  if (v === null) return '—';
  return v.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

const fmtPlSigned = (v: number | null): string => {
  if (v === null) return '—';
  const s = Math.abs(v).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return v >= 0 ? `+${s}` : `-${s.slice(1)}`;
};

const fmtDuration = (ms: number): string => {
  const totalMinutes = Math.round(ms / 60000);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
};

const fmtTime = (ts: string): string => {
  try {
    return new Date(ts).toLocaleString('en-US', {
      timeZone: 'America/New_York',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return ts;
  }
};

const fmtPct = (v: number): string => `${v.toFixed(1)}%`;

export const TradesModal: React.FC<TradesModalProps> = ({
  strategyName,
  markers,
  candles,
  performance,
  onClose,
}) => {
  const trades = useMemo(() => computeTrades(markers, candles), [markers, candles]);
  const wins = trades.filter((t) => t.isWin === true).length;
  const losses = trades.filter((t) => t.isWin === false).length;

  return (
    <div className="trades-modal-overlay" onClick={onClose}>
      <div className="trades-modal-content" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="trades-modal-header">
          <div className="trades-modal-title-group">
            <h2 className="trades-modal-title">Trade History</h2>
            {strategyName && (
              <span className="trades-modal-strategy-chip">{strategyName}</span>
            )}
          </div>
          <button className="trades-modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {/* Performance summary strip */}
        {performance && (
          <div className="trades-perf-strip">
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Total P/L</span>
              <strong className={`trades-perf-value ${performance.total_pl >= 0 ? 'pos' : 'neg'}`}>
                {fmtPlSigned(performance.total_pl)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Trades</span>
              <strong className="trades-perf-value">{performance.total_trades}</strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Win Rate</span>
              <strong
                className={`trades-perf-value ${
                  performance.win_rate >= 50 ? 'pos' : 'neg'
                }`}
              >
                {fmtPct(performance.win_rate)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Max DD</span>
              <strong className="trades-perf-value neg">
                {fmtDollars(performance.max_drawdown)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Sharpe</span>
              <strong
                className={`trades-perf-value ${
                  performance.sharpe_ratio >= 1
                    ? 'pos'
                    : performance.sharpe_ratio < 0
                    ? 'neg'
                    : ''
                }`}
              >
                {performance.sharpe_ratio.toFixed(2)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Avg Win</span>
              <strong className="trades-perf-value pos">
                {fmtDollars(performance.average_win)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Avg Loss</span>
              <strong className="trades-perf-value neg">
                {fmtDollars(performance.average_loss)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Max Loss</span>
              <strong className="trades-perf-value neg">
                {fmtDollars(performance.max_loss)}
              </strong>
            </div>
            <div className="trades-perf-stat">
              <span className="trades-perf-label">Final Equity</span>
              <strong className={`trades-perf-value ${performance.final_equity >= STARTING_CAPITAL ? 'pos' : 'neg'}`}>
                {fmtDollars(performance.final_equity)}
              </strong>
            </div>
          </div>
        )}

        {/* Table area */}
        <div className="trades-table-wrap">
          {trades.length === 0 ? (
            <div className="trades-empty-state">
              <div className="trades-empty-icon">📊</div>
              <p>No closed trades found.</p>
              <p className="trades-empty-hint">
                Apply a strategy with entry and exit rules to see individual trade history here.
              </p>
            </div>
          ) : (
            <table className="trades-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Side</th>
                  <th>Opened (ET)</th>
                  <th>Closed (ET)</th>
                  <th>Duration</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Shares</th>
                  <th>P/L</th>
                  <th>Result</th>
                  <th>Portfolio</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => (
                  <tr
                    key={trade.n}
                    className={
                      trade.isWin === true
                        ? 'row-win'
                        : trade.isWin === false
                        ? 'row-loss'
                        : ''
                    }
                  >
                    <td className="td-n">{trade.n}</td>
                    <td>
                      <span
                        className={`side-chip ${
                          trade.side === 'Long' ? 'side-long' : 'side-short'
                        }`}
                      >
                        {trade.side}
                      </span>
                    </td>
                    <td className="td-time">{fmtTime(trade.openTs)}</td>
                    <td className="td-time">{fmtTime(trade.closeTs)}</td>
                    <td className="td-dur">{fmtDuration(trade.durationMs)}</td>
                    <td className="td-price">{fmtDollars(trade.entryPrice)}</td>
                    <td className="td-price">{fmtDollars(trade.exitPrice)}</td>
                    <td className="td-shares">
                      {trade.shares > 0 ? trade.shares.toLocaleString() : '—'}
                    </td>
                    <td
                      className={`td-pl ${
                        trade.isWin === true
                          ? 'pos'
                          : trade.isWin === false
                          ? 'neg'
                          : ''
                      }`}
                    >
                      {fmtPlSigned(trade.grossPl)}
                    </td>
                    <td>
                      {trade.isWin === true && (
                        <span className="result-chip win">WIN</span>
                      )}
                      {trade.isWin === false && (
                        <span className="result-chip loss">LOSS</span>
                      )}
                      {trade.isWin === null && (
                        <span className="result-chip even">—</span>
                      )}
                    </td>
                    <td className="td-portfolio">{fmtDollars(trade.portfolioValue)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="tfoot-summary">
                  <td colSpan={8}>
                    {trades.length} closed trades &mdash; {wins}W / {losses}L
                  </td>
                  <td
                    className={`td-pl ${
                      (performance?.total_pl ?? 0) >= 0 ? 'pos' : 'neg'
                    }`}
                  >
                    {performance ? fmtPlSigned(performance.total_pl) : '—'}
                  </td>
                  <td>{performance ? fmtPct(performance.win_rate) : '—'}</td>
                  <td className="td-portfolio">
                    {performance ? fmtDollars(performance.final_equity) : '—'}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};
