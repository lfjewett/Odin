import {
  DragEvent,
  Fragment,
  MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { 
  useEventStream, 
  CandleEvent, 
  StatusUpdate, 
  SnapshotEvent,
  OverlayEvent,
  OverlayHistoryEvent
} from "./stream/useEventStream";
import { ChartView } from "./chart/ChartView";
import { MarketCandle, LineRecord } from "./types/events";
import type { OHLCBar } from "./history/fetchHistory";
import { useAgentSubscriptions, type AgentSubscription } from "./hooks/useAgentSubscriptions";
import AgentConfigModal from "./components/AgentConfigModal";
import AddIndicatorAgentModal from "./components/AddIndicatorAgentModal";
import { formatEasternTime24 } from "./utils/time";
import {
  activateWorkspace,
  deleteWorkspace,
  getWorkspace,
  listWorkspaces,
  saveWorkspace,
  type WorkspaceState,
} from "./workspace/workspaceApi";
import "./App.css";

const WATCHLIST_SYMBOLS = ["SPY", "QQQ", "IWM"];
const DEFAULT_WORKSPACE_NAME = "Default";

const MOCK_TRADE_RETURNS = [
  42, -36, 58, -22, 18, 64, -48, 29, -17, 53,
  -11, 35, -62, 72, -28, 19, 44, -31, 67, -39,
  24, -14, 39, -55, 71, -27, 15, 46, -33, 61,
  -41, 22, 37, -18, 55, -29, 48, -34, 69, -26,
  17, 41, -57, 75, -32, 21, 52, -37, 63, -24,
  26, -19, 43, -61, 79, -35, 20, 50, -38, 66,
  -25, 28, 40, -21, 57, -30, 49, -42, 73, -23,
  30, -16, 45, -53, 68, -34, 23, 54, -40, 62,
];

const MOCK_EQUITY_CURVE = [
  ...MOCK_TRADE_RETURNS.reduce<number[]>((curve, tradeReturn, index) => {
    const previousValue = index === 0 ? 10000 : curve[index - 1];
    curve.push(previousValue + tradeReturn);
    return curve;
  }, []),
];

const TOTAL_PL_VALUE = 18420;

const TRADE_MANAGER_METRICS = [
  { label: "Total Trades", value: "284" },
  { label: "Win Rate", value: "62.7%" },
  { label: "Max DD", value: "-$3,260" },
  { label: "Sharpe Ratio", value: "1.84" },
  { label: "Average Win", value: "+$210" },
  { label: "Average Loss", value: "-$128" },
  { label: "Max Loss", value: "-$740" },
];

type PanelSide = "left" | "right";
type WidgetId = "watchlist" | "overlayAgents" | "tradingBots2";

interface WidgetState {
  id: WidgetId;
  side: PanelSide;
  height: number;
}

const INITIAL_WIDGETS: WidgetState[] = [
  { id: "watchlist", side: "left", height: 330 },
  { id: "overlayAgents", side: "right", height: 240 },
  { id: "tradingBots2", side: "right", height: 420 },
];

export default function App() {
  const [selectedSymbol, setSelectedSymbol] = useState("SPY");
  const [symbolSearch, setSymbolSearch] = useState("");
  const [tickerInput, setTickerInput] = useState("SPY");
  const [intervalInput, setIntervalInput] = useState("1m");
  const [selectedTimeframe, setSelectedTimeframe] = useState(7); // days
  const [isLeftCollapsed, setIsLeftCollapsed] = useState(false);
  const [isRightCollapsed, setIsRightCollapsed] = useState(false);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [lastEventTime, setLastEventTime] = useState<string | null>(null);
  const [widgets, setWidgets] = useState<WidgetState[]>(INITIAL_WIDGETS);
  const [draggingWidgetId, setDraggingWidgetId] = useState<WidgetId | null>(null);
  const [candleHandler, setCandleHandler] = useState<
    ((candle: MarketCandle) => void) | null
  >(null);
  const [snapshotHandler, setSnapshotHandler] = useState<
    ((bars: OHLCBar[]) => void) | null
  >(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [modalAgentId, setModalAgentId] = useState<string | null>(null);
  const [isAgentModalOpen, setIsAgentModalOpen] = useState(false);
  const [isAddAgentModalOpen, setIsAddAgentModalOpen] = useState(false);
  const [clockNow, setClockNow] = useState(() => Date.now());
  const [isStrategyProfitable, setIsStrategyProfitable] = useState(true);
  const [workspaceNames, setWorkspaceNames] = useState<string[]>([]);
  const [currentWorkspaceName, setCurrentWorkspaceName] = useState<string>(DEFAULT_WORKSPACE_NAME);
  const [isWorkspaceMenuOpen, setIsWorkspaceMenuOpen] = useState(false);
  const lastSubscribeKeyRef = useRef<string | null>(null);
  const pendingSnapshotRef = useRef<SnapshotEvent | null>(null);
  const hasInitializedWorkspaceRef = useRef(false);
  const isApplyingWorkspaceRef = useRef(false);
  
  // Overlay data state for rendering overlays on chart
  const [overlayData, setOverlayData] = useState<Map<string, LineRecord[]>>(new Map());
  
  // Session management for ACP v0.3.0
  const sessionIdRef = useRef<string | null>(null);
  const getSessionId = useCallback(() => {
    if (!sessionIdRef.current) {
      sessionIdRef.current = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    }
    return sessionIdRef.current;
  }, []);

  const {
    subscriptions,
    loading: subscriptionsLoading,
    error: subscriptionsError,
    updateAgentStatus,
    discoverAgent,
    createSubscription,
    updateSubscription,
    deleteSubscription,
  } = useAgentSubscriptions();

  const handleAgentCreated = useCallback((_agentId: string) => {
  }, []);

  const selectedAgent = subscriptions?.find((a) => a.id === selectedAgentId) || null;
  const modalAgent = subscriptions?.find((a) => a.id === modalAgentId) || null;

  const captureWorkspaceState = useCallback((): WorkspaceState => {
    const subscriptionSnapshot = (subscriptions || []).map((agent) => ({
      id: agent.id,
      name: agent.name,
      agent_type: agent.agent_type,
      agent_url: agent.agent_url,
      config: (agent.config || {}) as Record<string, unknown>,
    }));

    return {
      selectedSymbol,
      intervalInput,
      selectedTimeframe,
      isLeftCollapsed,
      isRightCollapsed,
      widgets,
      selectedAgentId,
      agentSubscriptions: subscriptionSnapshot,
    };
  }, [
    subscriptions,
    selectedSymbol,
    intervalInput,
    selectedTimeframe,
    isLeftCollapsed,
    isRightCollapsed,
    widgets,
    selectedAgentId,
  ]);

  const applyWorkspaceState = useCallback(
    async (state: WorkspaceState) => {
      isApplyingWorkspaceRef.current = true;
      try {
        const normalizedSymbol = (state.selectedSymbol || "SPY").toUpperCase();
        setSelectedSymbol(normalizedSymbol);
        setTickerInput(normalizedSymbol);
        setIntervalInput(state.intervalInput || "1m");
        setSelectedTimeframe(state.selectedTimeframe || 7);
        setIsLeftCollapsed(Boolean(state.isLeftCollapsed));
        setIsRightCollapsed(Boolean(state.isRightCollapsed));

        const knownWidgetIds: WidgetId[] = ["watchlist", "overlayAgents", "tradingBots2"];
        const rawWidgets = Array.isArray(state.widgets) ? state.widgets : [];
        const normalizedWidgets = rawWidgets
          .filter((widget): widget is WidgetState => knownWidgetIds.includes(widget.id as WidgetId))
          .map((widget) => ({
            id: widget.id,
            side: widget.side === "left" ? "left" : "right",
            height: Math.max(160, Math.min(560, Number(widget.height) || 240)),
          }));
        setWidgets(normalizedWidgets.length > 0 ? normalizedWidgets : INITIAL_WIDGETS);

        const snapshotSubscriptions = Array.isArray(state.agentSubscriptions)
          ? state.agentSubscriptions
          : [];
        const subscriptionMap = new Map<string, AgentSubscription>();
        (subscriptions || []).forEach((agent) => {
          subscriptionMap.set(agent.id, agent);
        });

        for (const snapshot of snapshotSubscriptions) {
          const existing = subscriptionMap.get(snapshot.id);
          if (!existing) {
            continue;
          }

          const existingConfig = existing.config || {};
          const nextConfig = snapshot.config || {};
          const configChanged = JSON.stringify(existingConfig) !== JSON.stringify(nextConfig);
          const nameChanged = existing.name !== snapshot.name;

          if (configChanged || nameChanged) {
            await updateSubscription(snapshot.id, {
              name: snapshot.name,
              config: nextConfig,
            });
          }
        }

        setSelectedAgentId(state.selectedAgentId ?? null);
      } finally {
        isApplyingWorkspaceRef.current = false;
      }
    },
    [subscriptions, updateSubscription]
  );

  const refreshWorkspaceNames = useCallback(async (): Promise<{ names: string[]; active: string | null }> => {
    const listing = await listWorkspaces();
    const names = listing.workspaces.map((workspace) => workspace.name);
    setWorkspaceNames(names);
    return { names, active: listing.active_workspace };
  }, []);

  const handleSaveCurrentWorkspace = useCallback(async () => {
    const workspaceName = currentWorkspaceName || DEFAULT_WORKSPACE_NAME;
    await saveWorkspace(workspaceName, captureWorkspaceState());
    await activateWorkspace(workspaceName);
    await refreshWorkspaceNames();
    setIsWorkspaceMenuOpen(false);
  }, [captureWorkspaceState, currentWorkspaceName, refreshWorkspaceNames]);

  const handleSwitchWorkspace = useCallback(
    async (workspaceName: string) => {
      const record = await getWorkspace(workspaceName);
      await applyWorkspaceState(record.state);
      await activateWorkspace(workspaceName);
      setCurrentWorkspaceName(workspaceName);
      await refreshWorkspaceNames();
      setIsWorkspaceMenuOpen(false);
    },
    [applyWorkspaceState, refreshWorkspaceNames]
  );

  const handleCreateWorkspace = useCallback(async () => {
    const proposedName = window.prompt("New workspace name");
    const workspaceName = proposedName?.trim();
    if (!workspaceName) {
      return;
    }
    if (workspaceNames.includes(workspaceName)) {
      window.alert("A workspace with this name already exists.");
      return;
    }

    await saveWorkspace(workspaceName, captureWorkspaceState());
    await activateWorkspace(workspaceName);
    setCurrentWorkspaceName(workspaceName);
    await refreshWorkspaceNames();
    setIsWorkspaceMenuOpen(false);
  }, [captureWorkspaceState, refreshWorkspaceNames, workspaceNames]);

  const handleDeleteCurrentWorkspace = useCallback(async () => {
    if (!currentWorkspaceName) {
      return;
    }

    const confirmed = window.confirm(`Delete workspace \"${currentWorkspaceName}\"?`);
    if (!confirmed) {
      return;
    }

    const result = await deleteWorkspace(currentWorkspaceName);
    await refreshWorkspaceNames();

    if (result.active_workspace) {
      const record = await getWorkspace(result.active_workspace);
      await applyWorkspaceState(record.state);
      setCurrentWorkspaceName(result.active_workspace);
    }

    setIsWorkspaceMenuOpen(false);
  }, [applyWorkspaceState, currentWorkspaceName, refreshWorkspaceNames]);

  useEffect(() => {
    if (hasInitializedWorkspaceRef.current || subscriptionsLoading) {
      return;
    }

    hasInitializedWorkspaceRef.current = true;
    void (async () => {
      try {
        const listing = await listWorkspaces();
        if (listing.workspaces.length === 0) {
          await saveWorkspace(DEFAULT_WORKSPACE_NAME, captureWorkspaceState());
          await activateWorkspace(DEFAULT_WORKSPACE_NAME);
          setWorkspaceNames([DEFAULT_WORKSPACE_NAME]);
          setCurrentWorkspaceName(DEFAULT_WORKSPACE_NAME);
          return;
        }

        const workspaceName = listing.active_workspace || listing.workspaces[0].name;
        setWorkspaceNames(listing.workspaces.map((workspace) => workspace.name));
        setCurrentWorkspaceName(workspaceName);

        const record = await getWorkspace(workspaceName);
        await applyWorkspaceState(record.state);
        await activateWorkspace(workspaceName);
      } catch (error) {
        console.error("Failed to initialize workspace state:", error);
      }
    })();
  }, [applyWorkspaceState, captureWorkspaceState, subscriptionsLoading]);

  const resolveOverlayAgentId = useCallback(
    (incomingAgentId: string): string => {
      // Use incoming agent ID directly - backend sends correct runtime IDs
      // No prefix/suffix resolution needed for ACP v0.3.0 with unique instance IDs
      return incomingAgentId;
    },
    []
  );

  const overlayLineColors = useMemo(() => {
    const colors = new Map<string, string>();
    (subscriptions || []).forEach((agent) => {
      if (agent.agent_type !== "indicator") {
        return;
      }

      const configuredColor = agent.config?.line_color;
      if (typeof configuredColor === "string" && configuredColor.trim()) {
        colors.set(agent.id, configuredColor);
      }
    });
    console.log("[App] Overlay colors map:", Array.from(colors.entries()));
    return colors;
  }, [subscriptions]);

  useEffect(() => {
    console.log("[App] Agent selection effect triggered:", {
      selectedAgentId,
      subscriptions: subscriptions?.length,
      subscriptionsLoading,
      subscriptionsError
    });
    
    if (!selectedAgentId && subscriptions && subscriptions.length > 0) {
      const preferred = subscriptions.find((agent) => agent.output_schema === "ohlc");
      if (preferred) {
        console.log("[App] Auto-selecting agent:", preferred.id);
        setSelectedAgentId(preferred.id);
      }
    }
  }, [selectedAgentId, subscriptions, subscriptionsLoading, subscriptionsError]);

  const handleStatusUpdate = useCallback((update: StatusUpdate) => {
    updateAgentStatus(update.agent_id, update.status, update.error_message);
  }, [updateAgentStatus]);
  
  const handleSnapshot = useCallback((event: SnapshotEvent) => {
    const activeAgentId = selectedAgent?.id ?? selectedAgentId ?? null;
    const isMatchingSubscription =
      event.symbol === selectedSymbol && event.interval === intervalInput;

    // If no active selection exists yet, accept first snapshot stream.
    if (!activeAgentId && isMatchingSubscription) {
      setSelectedAgentId(event.agentId);
    }

    const expectedAgentId = activeAgentId ?? event.agentId;

    // Only process snapshots that match the current chart subscription.
    if (event.agentId === expectedAgentId && isMatchingSubscription) {
      console.log(`[App] Received snapshot for ${event.agentId}:`, event.bars.length, "bars");
      if (snapshotHandler) {
        snapshotHandler(event.bars);
      } else {
        pendingSnapshotRef.current = event;
      }
    }
  }, [selectedAgent, selectedAgentId, selectedSymbol, intervalInput, snapshotHandler]);

  const handleOverlayHistory = useCallback((event: OverlayHistoryEvent) => {
    const resolvedAgentId = resolveOverlayAgentId(event.agentId);
    console.log(`[App] Received overlay history for ${event.agentId} -> resolved to ${resolvedAgentId}:`, event.overlays.length, "records");
    if (event.schema === "line") {
      if (!event.overlays || event.overlays.length === 0) {
        return;
      }
      setOverlayData((prev) => {
        const updated = new Map(prev);
        updated.set(resolvedAgentId, event.overlays as LineRecord[]);
        console.log(`[App] Overlay data now has ${updated.size} agent(s):`, Array.from(updated.keys()));
        return updated;
      });
    }
  }, [resolveOverlayAgentId]);

  const handleOverlay = useCallback((event: OverlayEvent) => {
    const resolvedAgentId = resolveOverlayAgentId(event.agentId);
    console.log(`[App] Received overlay update for ${event.agentId} -> resolved to ${resolvedAgentId}:`, event.schema);
    if (event.schema === "line") {
      setOverlayData((prev) => {
        const updated = new Map(prev);
        const existing = updated.get(resolvedAgentId) || [];
        const record = event.record as LineRecord;
        
        // Find and update or append
        const index = existing.findIndex((r) => r.id === record.id);
        if (index >= 0) {
          existing[index] = record;
        } else {
          existing.push(record);
        }
        
        updated.set(resolvedAgentId, [...existing]);
        console.log(`[App] Overlay data now has ${updated.size} agent(s):`, Array.from(updated.keys()));
        return updated;
      });
    }
  }, [resolveOverlayAgentId]);

  const { isConnected, sendSubscribeRequest } = useEventStream(
    (event: CandleEvent) => {
      const activeAgentId = selectedAgent?.id ?? selectedAgentId ?? null;

      // If no active selection exists yet, accept first incoming stream.
      if (!activeAgentId || event.agentId === activeAgentId) {
        if (!activeAgentId) {
          setSelectedAgentId(event.agentId);
        }

        if (candleHandler) {
          candleHandler(event.candle);
        }

        setLastPrice(event.candle.close);
        setLastEventTime(event.candle.ts);
      }
    },
    handleStatusUpdate,
    handleSnapshot,
    handleOverlay,
    handleOverlayHistory
  );

  const handleCandleReceived = useCallback(
    (handler: (candle: MarketCandle) => void) => {
      setCandleHandler(() => handler);
    },
    []
  );
  
  const handleSnapshotRequested = useCallback(
    (handler: (bars: OHLCBar[]) => void) => {
      setSnapshotHandler(() => handler);

      const pendingSnapshot = pendingSnapshotRef.current;
      if (pendingSnapshot) {
        handler(pendingSnapshot.bars);
        pendingSnapshotRef.current = null;
      }
    },
    []
  );

  useEffect(() => {
    const nextSymbol = tickerInput.trim().toUpperCase();
    if (nextSymbol && nextSymbol !== selectedSymbol) {
      setSelectedSymbol(nextSymbol);
      setLastPrice(null);
      setLastEventTime(null);
    }
  }, [tickerInput, selectedSymbol]);

  useEffect(() => {
    if (isApplyingWorkspaceRef.current) {
      return;
    }

    const agentId = selectedAgent?.id ?? selectedAgentId;
    console.log("[App] Subscribe effect triggered:", {
      agentId,
      selectedSymbol,
      intervalInput,
      selectedTimeframe,
      lastSubscribeKey: lastSubscribeKeyRef.current
    });
    
    if (!agentId || !selectedSymbol) {
      console.log("[App] Subscribe skipped: missing agentId or symbol");
      return;
    }

    const subscribeKey = `${agentId}:${selectedSymbol}:${intervalInput}:${selectedTimeframe}`;
    if (lastSubscribeKeyRef.current === subscribeKey) {
      console.log("[App] Subscribe skipped: same key as last subscribe");
      return;
    }

    setOverlayData(new Map());

    const sent = sendSubscribeRequest({
      sessionId: getSessionId(),
      agentId,
      symbol: selectedSymbol,
      interval: intervalInput,
      timeframeDays: selectedTimeframe,
    });

    if (sent) {
      lastSubscribeKeyRef.current = subscribeKey;
      console.log("[App] Resubscribe requested:", subscribeKey);
    } else {
      console.log("[App] Subscribe queued until socket is ready:", subscribeKey);
    }
  }, [
    selectedAgent,
    selectedAgentId,
    selectedSymbol,
    intervalInput,
    selectedTimeframe,
    sendSubscribeRequest,
  ]);

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
                    onClick={() => {
                      setSelectedSymbol(symbol);
                      setTickerInput(symbol);
                    }}
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
            <button
              type="button"
              className="widget-config-button"
              onClick={() => setIsAddAgentModalOpen(true)}
            >
              Add Agent
            </button>
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
                        setModalAgentId(agent.id);
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

      const sparklineWidth = 300;
      const sparklineHeight = 54;
      const minValue = Math.min(...MOCK_EQUITY_CURVE);
      const maxValue = Math.max(...MOCK_EQUITY_CURVE);
      const range = Math.max(1, maxValue - minValue);
      const sparklinePoints = MOCK_EQUITY_CURVE.map((value, index) => {
        const x = (index / (MOCK_EQUITY_CURVE.length - 1)) * sparklineWidth;
        const y = sparklineHeight - ((value - minValue) / range) * sparklineHeight;
        return `${x},${y}`;
      }).join(" ");
      const totalPlValue = isStrategyProfitable ? Math.abs(TOTAL_PL_VALUE) : -Math.abs(TOTAL_PL_VALUE);
      const totalPlDisplay = `${totalPlValue >= 0 ? "+" : "-"}$${Math.abs(totalPlValue).toLocaleString()}`;
      const tradeManagerMetrics = [
        { label: "Total P/L", value: totalPlDisplay, tone: totalPlValue >= 0 ? "positive" : "negative" },
        ...TRADE_MANAGER_METRICS.map((metric) => ({ ...metric, tone: "neutral" })),
      ];
      const isRightTradeManager = widget.id === "tradingBots";
      const sparkBarWidth = sparklineWidth / MOCK_EQUITY_CURVE.length;
      const sparkColorClass = totalPlValue >= 0 ? "positive" : "negative";

      const profitToggle = (
        <button
          type="button"
          className={`trade-manager-profit-toggle ${isStrategyProfitable ? "on" : "off"}`}
          onClick={() => setIsStrategyProfitable((current) => !current)}
          aria-label="Toggle profitable or unprofitable strategy view"
        >
          <span>Profitable</span>
          <span>Unprofitable</span>
        </button>
      );

      const isLeftTradeManager = widget.id === "tradingBots2";

      return (
        <article className={`trade-manager-content ${isRightTradeManager ? "trade-manager-content--right" : ""} ${isLeftTradeManager ? "trade-manager-content--left" : ""}`}>

          <div
            className={`trade-manager-sparkline-wrap ${isRightTradeManager ? "trade-manager-sparkline-wrap--compact" : ""}`}
            aria-label="Equity curve mock"
          >
            <svg
              viewBox={`0 0 ${sparklineWidth} ${sparklineHeight}`}
              className={`trade-manager-sparkline ${isRightTradeManager ? `trade-manager-sparkline--bars ${sparkColorClass}` : ""}`}
              role="img"
            >
              {isRightTradeManager ? (
                MOCK_EQUITY_CURVE.map((value, index) => {
                  const normalizedHeight = Math.max(2, ((value - minValue) / range) * sparklineHeight);
                  return (
                    <rect
                      key={`spark-bar-${index}`}
                      x={index * sparkBarWidth + 0.5}
                      y={sparklineHeight - normalizedHeight}
                      width={Math.max(1, sparkBarWidth - 1)}
                      height={normalizedHeight}
                      rx={0.8}
                    />
                  );
                })
              ) : (
                <polyline fill="none" points={sparklinePoints} />
              )}
            </svg>
          </div>

          <div
            className={`trade-manager-metrics-grid ${
              isRightTradeManager ? "trade-manager-metrics-grid--single" : ""
            } ${isLeftTradeManager ? "trade-manager-metrics-grid--left" : ""}`}
          >
            {tradeManagerMetrics.map((metric) => (
              <div key={metric.label} className={`trade-manager-metric-item ${isLeftTradeManager ? "trade-manager-metric-item--left" : ""}`}>
                <span className="trade-manager-metric-label">{metric.label}</span>
                <strong
                  className={`trade-manager-metric-value ${
                    metric.tone === "positive"
                      ? "positive"
                      : metric.tone === "negative"
                      ? "negative"
                      : ""
                  }`}
                >
                  {metric.value}
                </strong>
              </div>
            ))}
          </div>

          {(isRightTradeManager || isLeftTradeManager) && (
            <div className="trade-manager-toggle-bottom">{profitToggle}</div>
          )}
        </article>
      );
    },
    [
      subscriptionsLoading,
      subscriptionsError,
      subscriptions,
      selectedAgentId,
      isAgentModalOpen,
      filteredWatchlist,
      selectedSymbol,
      symbolSearch,
      isStrategyProfitable,
    ]
  );

  const widgetTitle = (widgetId: WidgetId) => {
    if (widgetId === "watchlist") return "Watchlist";
    if (widgetId === "overlayAgents") return "Indicator Agents";
    return "Trade Manager";
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
                {widget.id === "tradingBots2" && (
                  <button
                    type="button"
                    className="widget-config-button"
                    onMouseDown={(event) => event.stopPropagation()}
                    onClick={() => {
                      console.log("[Trade Manager] Configure clicked");
                    }}
                  >
                    Configure
                  </button>
                )}
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
          <h1 className="app-title">ODIN Market Workspace v0.49</h1>
          <span className="symbol-chip">{selectedSymbol}</span>
        </div>
        <div className="topbar-tools">
          <div className="topbar-status-group">
            <div className="clock-pill" aria-label="Current Eastern Time">
              <span>{topbarClock}</span>
            </div>
            <div className="workspace-control">
              <button
                type="button"
                className="workspace-pill"
                onClick={() => setIsWorkspaceMenuOpen((current) => !current)}
                aria-haspopup="menu"
                aria-expanded={isWorkspaceMenuOpen}
                title="Workspace presets"
              >
                <span>Workspace: {currentWorkspaceName}</span>
                <span className="workspace-pill-chevron" aria-hidden="true">⌄</span>
              </button>
              {isWorkspaceMenuOpen && (
                <div className="workspace-menu" role="menu">
                  <div className="workspace-menu-list">
                    {workspaceNames.map((workspaceName) => (
                      <button
                        key={workspaceName}
                        type="button"
                        className={`workspace-menu-item ${workspaceName === currentWorkspaceName ? "active" : ""}`}
                        onClick={() => {
                          void handleSwitchWorkspace(workspaceName);
                        }}
                      >
                        {workspaceName}
                      </button>
                    ))}
                  </div>
                  <div className="workspace-menu-actions">
                    <button type="button" className="workspace-menu-action" onClick={() => void handleCreateWorkspace()}>
                      New Workspace
                    </button>
                    <button type="button" className="workspace-menu-action" onClick={() => void handleSaveCurrentWorkspace()}>
                      Save Workspace
                    </button>
                    <button
                      type="button"
                      className="workspace-menu-action danger"
                      onClick={() => void handleDeleteCurrentWorkspace()}
                    >
                      Delete Workspace
                    </button>
                  </div>
                </div>
              )}
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
              <img src="/logo.png" alt="Odin Logo" className="avatar-logo" />
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
                    onBlur={() => {
                      const nextSymbol = tickerInput.trim().toUpperCase();
                      if (nextSymbol) {
                        setSelectedSymbol(nextSymbol);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        const nextSymbol = tickerInput.trim().toUpperCase();
                        if (nextSymbol) {
                          setSelectedSymbol(nextSymbol);
                        }
                      }
                    }}
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
                onSnapshotRequested={handleSnapshotRequested}
                overlayData={overlayData}
                overlayLineColors={overlayLineColors}
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
        <span>Session: {getSessionId()}</span>
        <span>Feed: {statusText}</span>
        <span>Selected: {selectedSymbol}</span>
      </footer>

      <AgentConfigModal
        isOpen={isAgentModalOpen}
        subscription={modalAgent}
        onUpdate={async (id, update) => {
          await updateSubscription(id, { name: update.name, config: update.config });
        }}
        onDelete={async (id) => {
          await deleteSubscription(id);
          setOverlayData((current) => {
            const next = new Map(current);
            next.delete(id);
            return next;
          });
        }}
        onClose={() => {
          setIsAgentModalOpen(false);
          setModalAgentId(null);
        }}
      />

      <AddIndicatorAgentModal
        isOpen={isAddAgentModalOpen}
        onClose={() => setIsAddAgentModalOpen(false)}
        onCreated={(agent) => handleAgentCreated(agent.id)}
        discoverAgent={discoverAgent}
        createSubscription={createSubscription}
      />
    </div>
  );
}
