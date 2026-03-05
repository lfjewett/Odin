import {
  DragEvent,
  Fragment,
  MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useEventStream, CandleEvent, StatusUpdate } from "./stream/useEventStream";
import { ChartView } from "./chart/ChartView";
import { MarketCandle } from "./types/events";
import { useAgentSubscriptions } from "./hooks/useAgentSubscriptions";
import AgentConfigModal from "./components/AgentConfigModal";
import { formatEasternTime24 } from "./utils/time";
import "./App.css";

const WATCHLIST_SYMBOLS = ["SPY", "QQQ", "IWM"];

const BOTS_PLACEHOLDER = [
  { name: "Momentum Bot", status: "idle" },
  { name: "Mean Reversion", status: "disabled" },
];

type PanelSide = "left" | "right";
type WidgetId = "watchlist" | "overlayAgents" | "tradingBots";

interface WidgetState {
  id: WidgetId;
  side: PanelSide;
  height: number;
}

const INITIAL_WIDGETS: WidgetState[] = [
  { id: "watchlist", side: "left", height: 330 },
  { id: "overlayAgents", side: "right", height: 240 },
  { id: "tradingBots", side: "right", height: 220 },
];

export default function App() {
  const [selectedSymbol, setSelectedSymbol] = useState("SPY");
  const [symbolSearch, setSymbolSearch] = useState("");
  const [tickerInput, setTickerInput] = useState("SPY");
  const [intervalInput, setIntervalInput] = useState("1m");
  const [selectedTimeframe, setSelectedTimeframe] = useState(1); // days
  const [isLeftCollapsed, setIsLeftCollapsed] = useState(false);
  const [isRightCollapsed, setIsRightCollapsed] = useState(false);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [lastEventTime, setLastEventTime] = useState<string | null>(null);
  const [widgets, setWidgets] = useState<WidgetState[]>(INITIAL_WIDGETS);
  const [draggingWidgetId, setDraggingWidgetId] = useState<WidgetId | null>(null);
  const [candleHandler, setCandleHandler] = useState<
    ((candle: MarketCandle) => void) | null
  >(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [isAgentModalOpen, setIsAgentModalOpen] = useState(false);
  const [clockNow, setClockNow] = useState(() => Date.now());

  const {
    subscriptions,
    loading: subscriptionsLoading,
    error: subscriptionsError,
    updateAgentStatus,
  } = useAgentSubscriptions();

  const selectedAgent = subscriptions?.find((a) => a.id === selectedAgentId) || null;

  const handleStatusUpdate = useCallback((update: StatusUpdate) => {
    updateAgentStatus(update.agent_id, update.status, update.error_message);
  }, [updateAgentStatus]);

  const { isConnected } = useEventStream((event: CandleEvent) => {
    // Only process candles for the selected agent's subscription
    if (selectedAgent && event.subscriptionId === selectedAgent.id) {
      if (candleHandler) {
        candleHandler(event.candle);
      }

      setLastPrice(event.candle.close);
      setLastEventTime(event.candle.ts);
    }
  }, handleStatusUpdate);

  const handleCandleReceived = useCallback(
    (handler: (candle: MarketCandle) => void) => {
      setCandleHandler(() => handler);
    },
    []
  );

  useEffect(() => {
    const timer = setInterval(() => {
      setClockNow(Date.now());
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const statusColor = isConnected ? "#22c55e" : "#ef4444";
  const statusText = isConnected ? "Connected" : "Disconnected";

  const filteredWatchlist = useMemo(() => {
    const query = symbolSearch.trim().toLowerCase();
    if (!query) {
      return WATCHLIST_SYMBOLS;
    }

    return WATCHLIST_SYMBOLS.filter((symbol) =>
      symbol.toLowerCase().includes(query)
    );
  }, [symbolSearch]);

  const formattedPrice = lastPrice === null ? "—" : lastPrice.toFixed(2);
  const formattedEventTime = lastEventTime
    ? formatEasternTime24(lastEventTime)
    : "—";
  const topbarClock = formatEasternTime24(clockNow);

  // Extract candle type from agent config if available, default to "candlestick"
  const candleType: "candlestick" | "bar" | "line" | "area" = 
    (selectedAgent?.config?.candle_type as any || "candlestick");
  
  if (selectedAgentId) {
    console.log(`[App] selectedAgent (${selectedAgentId}):`, selectedAgent);
    console.log(`[App] candleType being used:`, candleType);
  }



  const moveWidget = useCallback(
    (widgetId: WidgetId, targetSide: PanelSide, targetIndex: number) => {
      setWidgets((currentWidgets) => {
        const movingWidget = currentWidgets.find((widget) => widget.id === widgetId);
        if (!movingWidget) {
          return currentWidgets;
        }

        const remaining = currentWidgets.filter((widget) => widget.id !== widgetId);
        const leftWidgets = remaining.filter((widget) => widget.side === "left");
        const rightWidgets = remaining.filter((widget) => widget.side === "right");

        const targetCollection = targetSide === "left" ? leftWidgets : rightWidgets;
        const boundedIndex = Math.max(0, Math.min(targetIndex, targetCollection.length));

        targetCollection.splice(boundedIndex, 0, {
          ...movingWidget,
          side: targetSide,
        });

        return [...leftWidgets, ...rightWidgets];
      });
    },
    []
  );

  const handleDragStart = useCallback(
    (event: DragEvent<HTMLDivElement>, widgetId: WidgetId) => {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", widgetId);
      setDraggingWidgetId(widgetId);
    },
    []
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>, side: PanelSide, index: number) => {
      event.preventDefault();
      const draggedId =
        draggingWidgetId ?? (event.dataTransfer.getData("text/plain") as WidgetId);

      if (draggedId) {
        moveWidget(draggedId, side, index);
      }

      setDraggingWidgetId(null);
    },
    [draggingWidgetId, moveWidget]
  );

  const handleResizeStart = useCallback(
    (event: MouseEvent<HTMLDivElement>, widgetId: WidgetId) => {
      event.preventDefault();

      const startY = event.clientY;
      const originalHeight =
        widgets.find((widget) => widget.id === widgetId)?.height ?? 240;

      const onMouseMove = (moveEvent: globalThis.MouseEvent) => {
        const delta = moveEvent.clientY - startY;
        const nextHeight = Math.max(160, Math.min(560, originalHeight + delta));

        setWidgets((currentWidgets) =>
          currentWidgets.map((widget) =>
            widget.id === widgetId ? { ...widget, height: nextHeight } : widget
          )
        );
      };

      const onMouseUp = () => {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      };

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [widgets]
  );

  const renderWidget = useCallback(
    (widget: WidgetState) => {
      if (widget.id === "watchlist") {
        return (
          <>
            <label className="field-label" htmlFor="symbol-search">
              Quick Symbol Search
            </label>
            <input
              id="symbol-search"
              className="symbol-search-input"
              type="text"
              value={symbolSearch}
              placeholder="Search symbol"
              onChange={(event) => setSymbolSearch(event.target.value.toUpperCase())}
            />

            <ul className="watchlist">
              {filteredWatchlist.map((symbol) => (
                <li key={symbol}>
                  <button
                    type="button"
                    className={`watchlist-item ${
                      selectedSymbol === symbol ? "active" : ""
                    }`}
                    onClick={() => setSelectedSymbol(symbol)}
                  >
                    {symbol}
                  </button>
                </li>
              ))}
            </ul>
          </>
        );
      }

      if (widget.id === "overlayAgents") {
        const agents = subscriptions || [];
        return (
          <>
            {subscriptionsLoading && <div className="loading-text">Loading agents...</div>}
            {!subscriptionsLoading && agents.length === 0 && subscriptionsError && (
              <div className="error-banner">⚠️ {subscriptionsError}</div>
            )}
            {!subscriptionsLoading && agents.length > 0 && (
              <ul className="status-list">
                {agents.map((agent) => (
                  <li key={agent.id} className="agent-item">
                    <button
                      type="button"
                      className="agent-name-button"
                      onClick={() => {
                        setSelectedAgentId(agent.id);
                        setIsAgentModalOpen(true);
                      }}
                    >
                      {agent.name}
                    </button>
                    <span className={`heartbeat ${agent.status}`}>{agent.status}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        );
      }

      return (
        <ul className="status-list">
          {BOTS_PLACEHOLDER.map((bot) => (
            <li key={bot.name}>
              <span>{bot.name}</span>
              <span className="muted-tag">{bot.status}</span>
            </li>
          ))}
        </ul>
      );
    },
    [subscriptionsLoading, subscriptionsError, subscriptions, selectedAgentId, isAgentModalOpen, filteredWatchlist, selectedSymbol, symbolSearch]
  );

  const widgetTitle = (widgetId: WidgetId) => {
    if (widgetId === "watchlist") return "Watchlist";
    if (widgetId === "overlayAgents") return "Overlay Agents";
    return "Trading Bots";
  };

  const renderWidgetArea = (side: PanelSide) => {
    const widgetsForSide = widgets.filter((widget) => widget.side === side);

    return (
      <div className="panel-content widget-area" data-side={side}>
        <div
          className={`widget-drop-zone ${draggingWidgetId ? "active" : ""}`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => handleDrop(event, side, 0)}
        />
        {widgetsForSide.map((widget, index) => (
          <Fragment key={widget.id}>
            <section className="widget-card" style={{ height: `${widget.height}px` }}>
              <div
                className="widget-card-header"
                draggable
                onDragStart={(event) => handleDragStart(event, widget.id)}
                onDragEnd={() => setDraggingWidgetId(null)}
              >
                <h3>{widgetTitle(widget.id)}</h3>
              </div>
              <div className="widget-body">{renderWidget(widget)}</div>
              <div
                className="widget-resize-handle"
                onMouseDown={(event) => handleResizeStart(event, widget.id)}
                role="separator"
                aria-orientation="horizontal"
                aria-label={`Resize ${widgetTitle(widget.id)} widget`}
              />
            </section>
            <div
              className={`widget-drop-zone ${draggingWidgetId ? "active" : ""}`}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => handleDrop(event, side, index + 1)}
            />
          </Fragment>
        ))}
      </div>
    );
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-title-group">
          <h1 className="app-title">ODIN Market Workspace v0.10</h1>
          <span className="symbol-chip">{selectedSymbol}</span>
        </div>
        <div className="topbar-tools">
          <div className="topbar-status-group">
            <div className="clock-pill" aria-label="Current Eastern Time">
              <span>{topbarClock}</span>
            </div>
            <div className="status-pill">
              <span
                className="status-dot"
                style={{ backgroundColor: statusColor }}
                aria-hidden="true"
              />
              <span>{statusText}</span>
            </div>
            <button 
              className="avatar-button" 
              type="button"
              title="Settings"
              aria-label="User settings"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z" fill="currentColor" opacity="0.1"/>
                <circle cx="12" cy="12" r="10"/>
                <path d="M8 10c0-1.1.9-2 2-2s2 .9 2 2-.9 2-2 2-2-.9-2-2zm6 0c0-1.1.9-2 2-2s2 .9 2 2-.9 2-2 2-2-.9-2-2z"/>
                <path d="M12 18c-2.5 0-4.8-1-6.5-2.5"/>
              </svg>
            </button>
          </div>
        </div>
      </header>

      <div className="workspace-grid">
        <aside className={`side-panel left ${isLeftCollapsed ? "collapsed" : ""}`}>
          <div className="side-panel-header">
            <button
              type="button"
              className="panel-toggle"
              onClick={() => setIsLeftCollapsed((current) => !current)}
              aria-label={isLeftCollapsed ? "Expand left panel" : "Collapse left panel"}
            >
              {isLeftCollapsed ? "»" : "«"}
            </button>
          </div>

          {!isLeftCollapsed ? (
            renderWidgetArea("left")
          ) : (
            <div className="panel-rail">Widgets</div>
          )}
        </aside>

        <main className="chart-stage">
          <div className="chart-card">
            <div className="chart-meta-row">
              <div className="chart-header-left">
                <div className="chart-ticker-control">
                  <input
                    type="text"
                    className="ticker-input"
                    value={tickerInput}
                    onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
                    placeholder="SPY"
                    maxLength={5}
                  />
                  <select 
                    className="interval-select"
                    value={intervalInput}
                    onChange={(e) => setIntervalInput(e.target.value)}
                  >
                    <option value="1m">1m</option>
                    <option value="5m">5m</option>
                    <option value="15m">15m</option>
                    <option value="1h">1h</option>
                    <option value="1d">1d</option>
                  </select>
                </div>
                <div className="chart-timeframe-control">
                  {[
                    { label: "1D", days: 1 },
                    { label: "1W", days: 7 },
                    { label: "1M", days: 30 },
                    { label: "3M", days: 90 },
                    { label: "6M", days: 180 },
                    { label: "1Y", days: 365 },
                  ].map((option) => (
                    <button
                      key={option.days}
                      onClick={() => setSelectedTimeframe(option.days)}
                      className={`timeframe-button ${selectedTimeframe === option.days ? "active" : ""}`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="chart-kpis-inline">
                <span className="kpi-item">Last <strong>{formattedPrice}</strong></span>
                <span className="kpi-item">Event Time <strong>{formattedEventTime}</strong></span>
              </div>
            </div>
            <div className="chart-container">
              <ChartView 
                onCandleReceived={handleCandleReceived} 
                selectedSymbol={selectedSymbol}
                selectedInterval={intervalInput}
                selectedTimeframe={selectedTimeframe}
                onTimeframeChange={setSelectedTimeframe}
                isLeftCollapsed={isLeftCollapsed}
                isRightCollapsed={isRightCollapsed}
                candleType={candleType}
              />
            </div>
          </div>
        </main>

        <aside className={`side-panel right ${isRightCollapsed ? "collapsed" : ""}`}>
          <div className="side-panel-header">
            <button
              type="button"
              className="panel-toggle"
              onClick={() => setIsRightCollapsed((current) => !current)}
              aria-label={isRightCollapsed ? "Expand right panel" : "Collapse right panel"}
            >
              {isRightCollapsed ? "«" : "»"}
            </button>
          </div>

          {!isRightCollapsed ? (
            renderWidgetArea("right")
          ) : (
            <div className="panel-rail">Widgets</div>
          )}
        </aside>
      </div>

      <footer className="workspace-footer">
        <span>Workspace: Phase 2 UI</span>
        <span>Feed: {statusText}</span>
        <span>Selected: {selectedSymbol}</span>
      </footer>

      <AgentConfigModal
        isOpen={isAgentModalOpen}
        subscription={selectedAgent}
        onClose={() => setIsAgentModalOpen(false)}
      />
    </div>
  );
}
