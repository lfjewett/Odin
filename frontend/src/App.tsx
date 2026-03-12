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
  OverlayHistoryEvent,
  StateEvent,
  SyncSnapshot,
} from "./stream/useEventStream";
import { ChartView } from "./chart/ChartView";
import { MarketCandle, OverlayRecord, OverlaySchema } from "./types/events";
import type { OHLCBar } from "./history/fetchHistory";
import { useAgentSubscriptions, type AgentSubscription } from "./hooks/useAgentSubscriptions";
import { useSyncCoordinator } from "./hooks/useSyncCoordinator";
import AgentConfigModal from "./components/AgentConfigModal";
import AddIndicatorAgentModal from "./components/AddIndicatorAgentModal";
import { TradeManagerModal } from "./components/TradeManagerModal";
import { ResearchModal, type ResearchOverlayEvaluation } from "./components/ResearchModal";
import { TradesModal } from "./components/TradesModal";
import ChartTimeControl from "./components/ChartTimeControl";
import { formatEasternTime24 } from "./utils/time";
import {
  applyTradeStrategy,
  activateWorkspace,
  createCsvExportJob,
  deleteWorkspace,
  getCsvExportDownloadUrl,
  getCsvExportJobStatus,
  listTradeStrategies,
  getWorkspace,
  listWorkspaces,
  saveWorkspace,
  type CsvExportJobStatus,
  type ApplyTradeStrategyResponse,
  type TradePerformance,
  type TradeMarker,
  type WorkspaceState,
} from "./workspace/workspaceApi";
import "./App.css";

const WATCHLIST_SYMBOLS = ["SPY", "QQQ", "IWM"];
const DEFAULT_WORKSPACE_NAME = "Default";
const SESSION_STORAGE_KEY = "odin.sessionId";
const TRADE_MARKERS_STORAGE_PREFIX = "odin.tradeMarkers";
const TRADE_PERFORMANCE_STORAGE_PREFIX = "odin.tradePerformance";
const RESEARCH_AGENT_ID = "__research__";

const STARTING_CAPITAL = 10_000;

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
  const todayDateString = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const defaultExportStartDate = useMemo(() => {
    const now = new Date();
    return `${now.getUTCFullYear() - 2}-01-01`;
  }, []);
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
  const [workspaceNames, setWorkspaceNames] = useState<string[]>([]);
  const [currentWorkspaceName, setCurrentWorkspaceName] = useState<string>(DEFAULT_WORKSPACE_NAME);
  const [isWorkspaceMenuOpen, setIsWorkspaceMenuOpen] = useState(false);
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [exportStartDate, setExportStartDate] = useState(defaultExportStartDate);
  const [exportEndDate, setExportEndDate] = useState(todayDateString);
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportJobStatus, setExportJobStatus] = useState<CsvExportJobStatus | null>(null);
  const [lastCompletedExportJobId, setLastCompletedExportJobId] = useState<string | null>(null);
  const [isExportSubmitting, setIsExportSubmitting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [showTradeManager, setShowTradeManager] = useState(false);
  const [showTradesModal, setShowTradesModal] = useState(false);
  const [showResearchModal, setShowResearchModal] = useState(false);
  const [researchDraftExpression, setResearchDraftExpression] = useState("");
  const lastSubscribeKeyRef = useRef<string | null>(null);
  const pendingSnapshotRef = useRef<SnapshotEvent | null>(null);
  const hasInitializedWorkspaceRef = useRef(false);
  const isApplyingWorkspaceRef = useRef(false);
  
  // Overlay data state for rendering overlays on chart
  const [overlayData, setOverlayData] = useState<Map<string, OverlayRecord[]>>(new Map());
  const [overlaySchemas, setOverlaySchemas] = useState<Map<string, OverlaySchema>>(new Map());
  const [tradeMarkers, setTradeMarkers] = useState<TradeMarker[]>([]);
  const [tradePerformance, setTradePerformance] = useState<TradePerformance | null>(null);
  const [appliedStrategyName, setAppliedStrategyName] = useState<string | null>(null);
  const [strategyCandlesById, setStrategyCandlesById] = useState<Map<string, OHLCBar>>(new Map());
  const markersHydratedKeyRef = useRef<string | null>(null);
  const performanceHydratedKeyRef = useRef<string | null>(null);
  const lastAutoApplyKeyRef = useRef<string | null>(null);
  const lastTradeAutoHealKeyRef = useRef<string | null>(null);
  const lastTradeAutoHealAtRef = useRef<number>(0);
  
  // Session management for ACP v0.4.x
  const sessionIdRef = useRef<string | null>(null);
  const getSessionId = useCallback(() => {
    if (!sessionIdRef.current) {
      try {
        const persisted = localStorage.getItem(SESSION_STORAGE_KEY);
        if (persisted && persisted.trim()) {
          sessionIdRef.current = persisted;
        }
      } catch {
        // ignore storage access failures
      }

      if (!sessionIdRef.current) {
        sessionIdRef.current = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        try {
          localStorage.setItem(SESSION_STORAGE_KEY, sessionIdRef.current);
        } catch {
          // ignore storage access failures
        }
      }
    }
    return sessionIdRef.current;
  }, []);

  const tradeMarkerStorageKey = useMemo(
    () => `${TRADE_MARKERS_STORAGE_PREFIX}:${getSessionId()}:${selectedSymbol}:${intervalInput}:${selectedTimeframe}`,
    [getSessionId, selectedSymbol, intervalInput, selectedTimeframe]
  );
  const tradePerformanceStorageKey = useMemo(
    () => `${TRADE_PERFORMANCE_STORAGE_PREFIX}:${getSessionId()}:${selectedSymbol}:${intervalInput}:${selectedTimeframe}`,
    [getSessionId, selectedSymbol, intervalInput, selectedTimeframe]
  );

  const {
    subscriptions,
    loading: subscriptionsLoading,
    error: subscriptionsError,
    stale: subscriptionsStale,
    lastSuccessfulRefreshAt,
    refresh: refreshSubscriptions,
    updateAgentStatus,
    discoverAgent,
    createSubscription,
    updateSubscription,
    deleteSubscription,
  } = useAgentSubscriptions();

  const {
    clientRevisions,
    staleDomains,
    tradeFreshness,
    onStateEvent,
    onSyncSnapshot,
    markTradeFresh,
    markTradeSyncing,
  } = useSyncCoordinator();

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
      appliedStrategyName,
      tradeMarkers,
      tradePerformance,
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
    appliedStrategyName,
    tradeMarkers,
    tradePerformance,
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
        setAppliedStrategyName(state.appliedStrategyName ?? null);
        setTradeMarkers(Array.isArray(state.tradeMarkers) ? state.tradeMarkers : []);
        setTradePerformance(state.tradePerformance ?? null);
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
    const stateToSave = captureWorkspaceState();
    console.log("[Workspace] Saving workspace state", {
      workspaceName,
      appliedStrategyName: stateToSave.appliedStrategyName,
      markerCount: stateToSave.tradeMarkers?.length || 0,
      hasPerformance: Boolean(stateToSave.tradePerformance),
      symbol: stateToSave.selectedSymbol,
      interval: stateToSave.intervalInput,
      timeframe: stateToSave.selectedTimeframe,
    });
    await saveWorkspace(workspaceName, stateToSave);
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

  const handleStartCsvExport = useCallback(async () => {
    const sessionId = getSessionId();
    if (!sessionId) {
      setExportError("Session is not ready yet.");
      return;
    }

    setIsExportSubmitting(true);
    setExportError(null);
    setLastCompletedExportJobId(null);

    try {
      const created = await createCsvExportJob(sessionId, {
        start_date: exportStartDate,
        end_date: exportEndDate,
        interval: intervalInput,
      });

      setExportJobId(created.job_id);
      setExportJobStatus(null);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Failed to start CSV export");
    } finally {
      setIsExportSubmitting(false);
    }
  }, [exportEndDate, exportStartDate, getSessionId, intervalInput]);

  const triggerCsvDownload = useCallback((sessionId: string, jobId: string) => {
    const url = getCsvExportDownloadUrl(sessionId, jobId);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "";
    anchor.rel = "noopener noreferrer";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  }, []);

  const handleDownloadCompletedExport = useCallback(() => {
    if (!lastCompletedExportJobId) {
      return;
    }
    const sessionId = getSessionId();
    if (!sessionId) {
      setExportError("Session is not ready yet.");
      return;
    }
    triggerCsvDownload(sessionId, lastCompletedExportJobId);
  }, [getSessionId, lastCompletedExportJobId, triggerCsvDownload]);

  useEffect(() => {
    if (!exportJobId) {
      return;
    }

    const sessionId = getSessionId();
    if (!sessionId) {
      return;
    }

    let cancelled = false;
    let intervalHandle: number | null = null;

    const poll = async () => {
      try {
        const status = await getCsvExportJobStatus(sessionId, exportJobId);
        if (cancelled) {
          return;
        }

        setExportJobStatus(status);

        if (status.status === "completed" && status.ready) {
          triggerCsvDownload(sessionId, exportJobId);
          setLastCompletedExportJobId(exportJobId);
          setExportJobId(null);
          return;
        }

        if (status.status === "failed") {
          setExportError(status.error || "CSV export failed");
          setExportJobId(null);
        }
      } catch (error) {
        if (!cancelled) {
          setExportError(error instanceof Error ? error.message : "Failed to poll export status");
          setExportJobId(null);
        }
      }
    };

    void poll();
    intervalHandle = window.setInterval(() => {
      void poll();
    }, 2500);

    return () => {
      cancelled = true;
      if (intervalHandle !== null) {
        window.clearInterval(intervalHandle);
      }
    };
  }, [exportJobId, getSessionId, triggerCsvDownload]);

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
      // No prefix/suffix resolution needed for ACP v0.4.x with unique instance IDs
      return incomingAgentId;
    },
    []
  );

  const buildOverlaySeriesKey = useCallback(
    (agentId: string, schema: OverlaySchema, outputId?: string | null): string => {
      const resolvedOutputId = outputId && outputId.trim() ? outputId.trim() : "default";
      return `${agentId}::${schema}::${resolvedOutputId}`;
    },
    []
  );

  const clearResearchOverlays = useCallback(() => {
    setOverlayData((prev) => {
      const next = new Map(prev);
      for (const key of Array.from(next.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          next.delete(key);
        }
      }
      return next;
    });
    setOverlaySchemas((prev) => {
      const next = new Map(prev);
      for (const key of Array.from(next.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          next.delete(key);
        }
      }
      return next;
    });
  }, []);

  const applyResearchOverlay = useCallback((evaluations: ResearchOverlayEvaluation[]) => {
    setOverlayData((prev) => {
      const next = new Map(prev);
      for (const key of Array.from(next.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          next.delete(key);
        }
      }
      evaluations.forEach(({ outputId, result }) => {
        const seriesKey = buildOverlaySeriesKey(RESEARCH_AGENT_ID, result.schema, outputId);
        next.set(seriesKey, result.records as OverlayRecord[]);
      });
      return next;
    });

    setOverlaySchemas((prev) => {
      const next = new Map(prev);
      for (const key of Array.from(next.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          next.delete(key);
        }
      }
      evaluations.forEach(({ outputId, result }) => {
        const seriesKey = buildOverlaySeriesKey(RESEARCH_AGENT_ID, result.schema, outputId);
        next.set(seriesKey, result.schema);
      });
      return next;
    });
  }, [buildOverlaySeriesKey]);

  // Helper to check if an agent is visible
  const isAgentVisible = useCallback((agentId: string): boolean => {
    const agent = subscriptions?.find((a) => a.id === agentId);
    return agent?.config?.visible !== false;
  }, [subscriptions]);

  const overlayLineColors = useMemo(() => {
    const colors = new Map<string, string>();
    (subscriptions || []).forEach((agent) => {
      if (agent.agent_type !== "indicator") {
        return;
      }

      if (agent.config?.visible === false) {
        return;
      }

      const configuredColor = agent.config?.line_color;
      if (typeof configuredColor === "string" && configuredColor.trim()) {
        colors.set(agent.id, configuredColor);
      }
    });
    return colors;
  }, [subscriptions]);

  /** agentId → true for any indicator that has force_subgraph saved in config */
  const overlaySubgraphForced = useMemo(() => {
    const forced = new Map<string, boolean>();
    forced.set(RESEARCH_AGENT_ID, true);
    (subscriptions || []).forEach((agent) => {
      if (agent.agent_type !== "indicator") return;
      if (agent.config?.force_subgraph === true) {
        forced.set(agent.id, true);
      }
    });
    return forced;
  }, [subscriptions]);

  const overlayAreaStyles = useMemo(() => {
    const styles = new Map<
      string,
      {
        useSourceStyle: boolean;
        showLabels: boolean;
        fillMode: "solid" | "conditional";
        opacityPercent: number;
        conditionalUpColor?: string;
        conditionalDownColor?: string;
      }
    >();
    (subscriptions || []).forEach((agent) => {
      if (agent.agent_type !== "indicator") {
        return;
      }

      const hasAreaOutput = Array.isArray(agent.outputs)
        && agent.outputs.some((output) => output?.schema === "area");
      if (!hasAreaOutput) {
        return;
      }

      const hasExplicitUiAreaStyle =
        agent.config?.area_fill_mode !== undefined
        || agent.config?.area_fill_opacity !== undefined
        || agent.config?.area_conditional_up_color !== undefined
        || agent.config?.area_conditional_down_color !== undefined;

      const useSourceStyle = agent.config?.area_use_source_style === true
        || (agent.config?.area_use_source_style !== false && !hasExplicitUiAreaStyle);

      const fillMode = agent.config?.area_fill_mode === "solid" ? "solid" : "conditional";
      const rawOpacity = Number(agent.config?.area_fill_opacity);
      const opacityPercent = Number.isFinite(rawOpacity)
        ? Math.max(0, Math.min(100, rawOpacity))
        : 50;
      const conditionalUpColor = typeof agent.config?.area_conditional_up_color === "string"
        ? agent.config.area_conditional_up_color.trim()
        : "";
      const conditionalDownColor = typeof agent.config?.area_conditional_down_color === "string"
        ? agent.config.area_conditional_down_color.trim()
        : "";

      styles.set(agent.id, {
        useSourceStyle,
        showLabels: agent.config?.area_show_labels !== false,
        fillMode,
        opacityPercent,
        conditionalUpColor: conditionalUpColor || undefined,
        conditionalDownColor: conditionalDownColor || undefined,
      });
    });
    return styles;
  }, [subscriptions]);

  useEffect(() => {
    if (!selectedAgentId && subscriptions && subscriptions.length > 0) {
      const preferred = subscriptions.find((agent) => agent.output_schema === "ohlc");
      if (preferred) {
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
      setStrategyCandlesById(() => {
        const next = new Map<string, OHLCBar>();
        event.bars.forEach((bar) => {
          next.set(bar.id, bar);
        });
        return next;
      });
      if (snapshotHandler) {
        snapshotHandler(event.bars);
      } else {
        pendingSnapshotRef.current = event;
      }
    }
  }, [selectedAgent, selectedAgentId, selectedSymbol, intervalInput, snapshotHandler]);

  const handleOverlayHistory = useCallback((event: OverlayHistoryEvent) => {
    const resolvedAgentId = resolveOverlayAgentId(event.agentId);

    // Skip if agent is not visible
    if (!isAgentVisible(resolvedAgentId)) {
      return;
    }
    
    if (!event.overlays || event.overlays.length === 0) {
      return;
    }

    const groupedByOutputId = new Map<string, OverlayRecord[]>();
    for (const overlay of event.overlays) {
      const outputId = String((overlay as Record<string, unknown>).output_id ?? "default");
      const existing = groupedByOutputId.get(outputId) || [];
      existing.push(overlay);
      groupedByOutputId.set(outputId, existing);
    }

    setOverlayData((prev) => {
      const updated = new Map(prev);
      for (const [outputId, overlays] of groupedByOutputId.entries()) {
        const key = buildOverlaySeriesKey(resolvedAgentId, event.schema, outputId);
        updated.set(key, overlays);
      }
      return updated;
    });

    setOverlaySchemas((prev) => {
      const updated = new Map(prev);
      for (const outputId of groupedByOutputId.keys()) {
        const key = buildOverlaySeriesKey(resolvedAgentId, event.schema, outputId);
        updated.set(key, event.schema);
      }
      return updated;
    });
  }, [resolveOverlayAgentId, buildOverlaySeriesKey, isAgentVisible]);

  const handleOverlay = useCallback((event: OverlayEvent) => {
    const resolvedAgentId = resolveOverlayAgentId(event.agentId);

    // Skip if agent is not visible
    if (!isAgentVisible(resolvedAgentId)) {
      return;
    }
    
    const outputId = String((event.record as Record<string, unknown>).output_id ?? "default");
    const seriesKey = buildOverlaySeriesKey(resolvedAgentId, event.schema, outputId);

    setOverlayData((prev) => {
      const updated = new Map(prev);
      const existing = [...(updated.get(seriesKey) || [])];
      const record = event.record as OverlayRecord;

      const index = existing.findIndex((r) => r.id === record.id);
      if (index >= 0) {
        existing[index] = record;
      } else {
        existing.push(record);
      }

      updated.set(seriesKey, existing);
      return updated;
    });

    setOverlaySchemas((prev) => {
      const updated = new Map(prev);
      updated.set(seriesKey, event.schema);
      return updated;
    });
  }, [resolveOverlayAgentId, buildOverlaySeriesKey, isAgentVisible]);

  const handleStateEvent = useCallback(
    (event: StateEvent) => {
      onStateEvent(event as any);

      if (event.domain === "agent") {
        void refreshSubscriptions(true);
      }

      const currentSessionId = getSessionId();
      if (!event.session_id || event.session_id !== currentSessionId) {
        return;
      }

      if (event.event_name === "trade.results.invalidated") {
        markTradeSyncing();
        return;
      }

      if (event.event_name === "trade.results.recomputed") {
        const payload = event.payload || {};
        const result = (payload.result || null) as ApplyTradeStrategyResponse | null;
        if (!result) {
          return;
        }
        setTradeMarkers(result.markers || []);
        setTradePerformance(result.performance || null);
        if (result.strategy_name) {
          setAppliedStrategyName(result.strategy_name);
        }
        markTradeFresh();
      }
    },
    [getSessionId, markTradeFresh, markTradeSyncing, onStateEvent, refreshSubscriptions]
  );

  const handleSyncSnapshot = useCallback(
    (snapshot: SyncSnapshot) => {
      onSyncSnapshot(snapshot as any);

      if (snapshot.stale_domains.includes("agent")) {
        void refreshSubscriptions(true);
      }

      if (!snapshot.stale_domains.includes("trade")) {
        return;
      }

      const currentSessionId = getSessionId();
      const tradeSession = (snapshot.trade_sessions || []).find(
        (entry) => entry.session_id === currentSessionId
      );
      if (!tradeSession) {
        if (!appliedStrategyName || strategyCandlesById.size === 0) {
          return;
        }

        const tradeRevision = snapshot.server_revisions?.trade ?? 0;
        const healKey = `${currentSessionId}:${appliedStrategyName}:${tradeRevision}`;
        const now = Date.now();
        const recentlyAttempted =
          lastTradeAutoHealKeyRef.current === healKey && now - lastTradeAutoHealAtRef.current < 15000;

        if (recentlyAttempted) {
          return;
        }

        lastTradeAutoHealKeyRef.current = healKey;
        lastTradeAutoHealAtRef.current = now;

        console.log("[TradeManager] Auto-healing stale trade state", {
          currentSessionId,
          appliedStrategyName,
          tradeRevision,
        });

        void (async () => {
          try {
            markTradeSyncing();
            const applied = await applyTradeStrategy(currentSessionId, { strategy_name: appliedStrategyName });
            setTradeMarkers(applied.markers || []);
            setTradePerformance(applied.performance || null);
            if (applied.strategy_name) {
              setAppliedStrategyName(applied.strategy_name);
            }
            markTradeFresh();
          } catch (error) {
            console.warn("[TradeManager] Auto-heal apply failed", error);
          }
        })();
        return;
      }

      const result = tradeSession.result as ApplyTradeStrategyResponse;
      setTradeMarkers(result.markers || []);
      setTradePerformance(result.performance || null);
      if (result.strategy_name) {
        setAppliedStrategyName(result.strategy_name);
      }
      markTradeFresh();
    },
    [
      appliedStrategyName,
      getSessionId,
      markTradeFresh,
      markTradeSyncing,
      onSyncSnapshot,
      refreshSubscriptions,
      strategyCandlesById.size,
    ]
  );

  const { isConnected, sendSubscribeRequest } = useEventStream(
    (event: CandleEvent) => {
      const activeAgentId = selectedAgent?.id ?? selectedAgentId ?? null;

      // If no active selection exists yet, accept first incoming stream.
      if (!activeAgentId || event.agentId === activeAgentId) {
        if (!activeAgentId) {
          setSelectedAgentId(event.agentId);
        }

        setStrategyCandlesById((prev) => {
          const next = new Map(prev);
          next.set(event.candle.id, {
            id: event.candle.id,
            seq: event.candle.seq,
            rev: event.candle.rev,
            bar_state: event.candle.bar_state as OHLCBar["bar_state"],
            ts: event.candle.ts,
            open: event.candle.open,
            high: event.candle.high,
            low: event.candle.low,
            close: event.candle.close,
            volume: event.candle.volume,
          });
          return next;
        });

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
    handleOverlayHistory,
    handleStateEvent,
    handleSyncSnapshot,
    () => clientRevisions
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
    if (!agentId || !selectedSymbol) {
      return;
    }

    const subscribeKey = `${agentId}:${selectedSymbol}:${intervalInput}:${selectedTimeframe}`;
    if (lastSubscribeKeyRef.current === subscribeKey) {
      return;
    }

    setOverlayData(new Map());
    setOverlaySchemas(new Map());
    setStrategyCandlesById(new Map());
    setTradeMarkers([]);
    setTradePerformance(null);
    markTradeSyncing();

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
    markTradeSyncing,
  ]);

  useEffect(() => {
    const timer = setInterval(() => {
      setClockNow(Date.now());
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(tradeMarkerStorageKey);
      const parsed = raw ? (JSON.parse(raw) as TradeMarker[]) : [];
      setTradeMarkers(Array.isArray(parsed) ? parsed : []);
      markersHydratedKeyRef.current = tradeMarkerStorageKey;
    } catch {
      setTradeMarkers([]);
      markersHydratedKeyRef.current = tradeMarkerStorageKey;
    }
  }, [tradeMarkerStorageKey]);

  useEffect(() => {
    if (markersHydratedKeyRef.current !== tradeMarkerStorageKey) {
      return;
    }
    try {
      localStorage.setItem(tradeMarkerStorageKey, JSON.stringify(tradeMarkers));
    } catch {
      // ignore storage access failures
    }
  }, [tradeMarkers, tradeMarkerStorageKey]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(tradePerformanceStorageKey);
      const parsed = raw ? (JSON.parse(raw) as TradePerformance) : null;
      setTradePerformance(parsed && typeof parsed === "object" ? parsed : null);
      performanceHydratedKeyRef.current = tradePerformanceStorageKey;
    } catch {
      setTradePerformance(null);
      performanceHydratedKeyRef.current = tradePerformanceStorageKey;
    }
  }, [tradePerformanceStorageKey]);

  useEffect(() => {
    if (performanceHydratedKeyRef.current !== tradePerformanceStorageKey) {
      return;
    }
    try {
      if (tradePerformance) {
        localStorage.setItem(tradePerformanceStorageKey, JSON.stringify(tradePerformance));
      } else {
        localStorage.removeItem(tradePerformanceStorageKey);
      }
    } catch {
      // ignore storage access failures
    }
  }, [tradePerformance, tradePerformanceStorageKey]);

  useEffect(() => {
    const autoApplyStrategy = async () => {
      const sessionId = getSessionId();
      const applyKey = `${sessionId}:${selectedSymbol}:${intervalInput}:${selectedTimeframe}`;
      if (lastAutoApplyKeyRef.current === applyKey) {
        console.log("[TradeManager] Auto-apply skipped (already attempted)", { applyKey });
        return;
      }

      if (strategyCandlesById.size === 0 || !isConnected) {
        console.log("[TradeManager] Auto-apply waiting for candles/connection", {
          candles: strategyCandlesById.size,
          isConnected,
          applyKey,
        });
        return;
      }

      try {
        console.log("[TradeManager] Auto-apply checking saved strategies", { sessionId, applyKey });
        const listed = await listTradeStrategies(sessionId);
        if (!listed.strategies || listed.strategies.length === 0) {
          console.log("[TradeManager] Auto-apply found no saved strategies", { sessionId });
          lastAutoApplyKeyRef.current = applyKey;
          return;
        }

        const hasAppliedNamedStrategy =
          typeof appliedStrategyName === "string" &&
          appliedStrategyName.trim().length > 0;
        const strategyName = hasAppliedNamedStrategy
          ? appliedStrategyName!.trim()
          : listed.strategies[0].name;
        console.log("[TradeManager] Auto-applying strategy", { strategyName, sessionId, applyKey });
        const applied = await applyTradeStrategy(sessionId, { strategy_name: strategyName });
        setTradeMarkers(applied.markers || []);
        setTradePerformance(applied.performance || null);
        setAppliedStrategyName(applied.strategy_name || strategyName);
        console.log("[TradeManager] Auto-apply completed", {
          strategyName: applied.strategy_name || strategyName,
          markerCount: applied.marker_count,
          hasPerformance: Boolean(applied.performance),
        });
        lastAutoApplyKeyRef.current = applyKey;
      } catch (err) {
        console.warn("[TradeManager] Auto-apply failed:", err);
      }
    };

    void autoApplyStrategy();
  }, [
    getSessionId,
    selectedSymbol,
    intervalInput,
    selectedTimeframe,
    strategyCandlesById.size,
    isConnected,
    appliedStrategyName,
  ]);

  useEffect(() => {
    console.log("[TradeManager] Stats inputs changed", {
      appliedStrategyName,
      markerCount: tradeMarkers.length,
      hasPerformance: Boolean(tradePerformance),
      candleCount: strategyCandlesById.size,
    });
  }, [appliedStrategyName, tradeMarkers.length, tradePerformance, strategyCandlesById.size]);

  // Clean up overlay data for hidden agents
  useEffect(() => {
    const visibleAgentIds = new Set(
      (subscriptions || [])
        .filter((agent) => agent.config?.visible !== false)
        .map((agent) => agent.id)
    );

    setOverlayData((prev) => {
      const updated = new Map(prev);
      let changed = false;
      
      for (const key of Array.from(updated.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          continue;
        }
        // Extract agent ID from overlay series key (format: agentId::schema::outputId)
        const agentId = key.split("::")[0];
        if (!visibleAgentIds.has(agentId)) {
          updated.delete(key);
          changed = true;
        }
      }
      
      if (changed) {
        console.log(`[App] Removed overlay data for hidden agents. Remaining: ${updated.size} series`);
      }
      return changed ? updated : prev;
    });

    setOverlaySchemas((prev) => {
      const updated = new Map(prev);
      let changed = false;
      
      for (const key of Array.from(updated.keys())) {
        if (key.startsWith(`${RESEARCH_AGENT_ID}::`)) {
          continue;
        }
        // Extract agent ID from schema key (format: agentId::schema::outputId)
        const agentId = key.split("::")[0];
        if (!visibleAgentIds.has(agentId)) {
          updated.delete(key);
          changed = true;
        }
      }
      
      return changed ? updated : prev;
    });
  }, [subscriptions]);

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

  const strategyPerformance = useMemo(() => {
    const formatCurrency = (value: number) =>
      `${value > 0 ? "+" : value < 0 ? "-" : ""}$${Math.abs(value).toLocaleString(undefined, {
        maximumFractionDigits: 0,
      })}`;
    if (!tradePerformance) {
      return {
        equityCurve: [STARTING_CAPITAL],
        metrics: [
          { label: "Total P/L", value: "—", tone: "neutral" as const },
          { label: "Total Trades", value: "—", tone: "neutral" as const },
          { label: "Win Rate", value: "—", tone: "neutral" as const },
          { label: "Max DD", value: "—", tone: "neutral" as const },
          { label: "Sharpe Ratio", value: "—", tone: "neutral" as const },
          { label: "Average Win", value: "—", tone: "neutral" as const },
          { label: "Average Loss", value: "—", tone: "neutral" as const },
          { label: "Max Loss", value: "—", tone: "neutral" as const },
        ],
      };
    }

    const curve = (tradePerformance.equity_curve || [])
      .map((point) => Number(point?.equity))
      .filter((value) => Number.isFinite(value));

    const totalPl = Number(tradePerformance.total_pl ?? 0);
    const totalTrades = Number(tradePerformance.total_trades ?? 0);
    const winRate = Number(tradePerformance.win_rate ?? 0);
    const maxDrawdown = Math.abs(Number(tradePerformance.max_drawdown ?? 0));
    const sharpe = Number(tradePerformance.sharpe_ratio ?? 0);
    const averageWin = Number(tradePerformance.average_win ?? 0);
    const averageLoss = Number(tradePerformance.average_loss ?? 0);
    const maxLoss = Number(tradePerformance.max_loss ?? 0);

    console.log("[TradeManager] Rendering backend performance", {
      curvePoints: curve.length,
      totalPl,
      totalTrades,
      winRate,
      maxDrawdown,
      sharpe,
      averageWin,
      averageLoss,
      maxLoss,
    });

    return {
      equityCurve: curve.length > 0 ? curve : [STARTING_CAPITAL],
      metrics: [
        { label: "Total P/L", value: formatCurrency(totalPl), tone: totalPl > 0 ? "positive" as const : totalPl < 0 ? "negative" as const : "neutral" as const },
        { label: "Total Trades", value: Number.isFinite(totalTrades) ? String(Math.trunc(totalTrades)) : "—", tone: "neutral" as const },
        { label: "Win Rate", value: Number.isFinite(winRate) ? `${winRate.toFixed(1)}%` : "—", tone: winRate >= 50 ? "positive" as const : totalTrades > 0 ? "negative" as const : "neutral" as const },
        { label: "Max DD", value: formatCurrency(-maxDrawdown), tone: maxDrawdown > 0 ? "negative" as const : "neutral" as const },
        { label: "Sharpe Ratio", value: Number.isFinite(sharpe) ? sharpe.toFixed(2) : "—", tone: sharpe > 1 ? "positive" as const : sharpe < 0 ? "negative" as const : "neutral" as const },
        { label: "Average Win", value: formatCurrency(averageWin), tone: averageWin > 0 ? "positive" as const : "neutral" as const },
        { label: "Average Loss", value: formatCurrency(averageLoss), tone: averageLoss < 0 ? "negative" as const : "neutral" as const },
        { label: "Max Loss", value: formatCurrency(maxLoss), tone: maxLoss < 0 ? "negative" as const : "neutral" as const },
      ],
    };
  }, [tradePerformance]);

  const formattedPrice = lastPrice === null ? "—" : lastPrice.toFixed(2);
  const formattedEventTime = lastEventTime
    ? formatEasternTime24(lastEventTime)
    : "—";
  const topbarClock = formatEasternTime24(clockNow);

  // Extract candle type from agent config if available, default to "candlestick"
  const candleType: "candlestick" | "bar" | "line" | "area" = 
    (selectedAgent?.config?.candle_type as any || "candlestick");

  const handleRunTest = async () => {
    try {
      const sessionId = getSessionId();
      console.log("[RunTest] Starting manual test...", { 
        sessionId, 
        appliedStrategyName,
        markerCount: tradeMarkers.length,
        hasPerformance: Boolean(tradePerformance),
        candleCount: strategyCandlesById.size,
      });

      // List saved strategies
      const listed = await listTradeStrategies(sessionId);
      console.log("[RunTest] Listed strategies:", listed);

      if (!listed.strategies || listed.strategies.length === 0) {
        console.warn("[RunTest] No saved strategies found!");
        alert("No saved strategies found. Please configure a strategy first.");
        return;
      }

      const strategyName = listed.strategies[0].name;
      console.log("[RunTest] Applying strategy:", strategyName);

      // Apply the strategy
      const applied = await applyTradeStrategy(sessionId, { strategy_name: strategyName });
      
      console.log("[RunTest] ========== APPLY RESULT ==========");
      console.log("[RunTest] Strategy Name:", applied.strategy_name);
      console.log("[RunTest] Marker Count:", applied.marker_count);
      console.log("[RunTest] Markers:", applied.markers);
      console.log("[RunTest] Performance Object:", applied.performance);
      if (applied.performance) {
        console.log("[RunTest] Performance Details:");
        console.log("  - Total P/L:", applied.performance.total_pl);
        console.log("  - Total Trades:", applied.performance.total_trades);
        console.log("  - Win Rate:", applied.performance.win_rate);
        console.log("  - Max Drawdown:", applied.performance.max_drawdown);
        console.log("  - Sharpe Ratio:", applied.performance.sharpe_ratio);
        console.log("  - Equity Curve Points:", applied.performance.equity_curve?.length || 0);
      } else {
        console.log("[RunTest] ⚠️ Performance is NULL or undefined!");
      }
      console.log("[RunTest] =====================================");

      // Update state
      setTradeMarkers(applied.markers || []);
      setTradePerformance(applied.performance || null);
      setAppliedStrategyName(applied.strategy_name || strategyName);

      console.log("[RunTest] State updated. New values:");
      console.log("  - Markers:", applied.markers?.length || 0);
      console.log("  - Performance:", applied.performance ? "Present" : "NULL");
      
    } catch (err) {
      console.error("[RunTest] Failed:", err);
      alert(`Test failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

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
            {subscriptionsStale && (
              <div className="error-banner">
                ⚠️ Agent state may be stale{lastSuccessfulRefreshAt ? ` (last sync ${new Date(lastSuccessfulRefreshAt).toLocaleTimeString()})` : ""}
              </div>
            )}
          </>
        );
      }

      const sparklineWidth = 300;
      const sparklineHeight = 54;
      const liveEquityCurve = strategyPerformance.equityCurve.length > 0 ? strategyPerformance.equityCurve : [STARTING_CAPITAL];
      const minValue = Math.min(...liveEquityCurve);
      const maxValue = Math.max(...liveEquityCurve);
      const range = Math.max(1, maxValue - minValue);
      const sparklinePoints = liveEquityCurve.map((value, index) => {
        const x = (index / Math.max(1, liveEquityCurve.length - 1)) * sparklineWidth;
        const y = sparklineHeight - ((value - minValue) / range) * sparklineHeight;
        return `${x},${y}`;
      }).join(" ");
      const tradeManagerMetrics = strategyPerformance.metrics;
      const totalPlValue = liveEquityCurve[liveEquityCurve.length - 1] - STARTING_CAPITAL;
      const isRightTradeManager = widget.id === "tradingBots";
      const sparkBarWidth = sparklineWidth / Math.max(1, liveEquityCurve.length);
      const sparkColorClass = totalPlValue >= 0 ? "positive" : "negative";

      const isLeftTradeManager = widget.id === "tradingBots2";

      return (
        <>
          {isLeftTradeManager && appliedStrategyName && (
            <div className="widget-active-strategy">
              <span className="widget-active-strategy-label">Active Strategy</span>
              <span className="widget-active-strategy-name">{appliedStrategyName}</span>
              {(tradeFreshness !== "fresh" || staleDomains.includes("trade")) && (
                <span className="widget-active-strategy-label">
                  {tradeFreshness === "syncing" ? "Syncing…" : "Stale"}
                </span>
              )}
            </div>
          )}
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
                liveEquityCurve.map((value, index) => {
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

          {isLeftTradeManager && (
            <div className="trade-manager-actions-bottom">
              <button
                type="button"
                className="widget-config-button"
                onClick={() => setShowTradesModal(true)}
                title="Show trade history"
              >
                Trades
              </button>
              <button
                type="button"
                className="widget-config-button"
                onClick={() => setShowResearchModal(true)}
                title="Open research strategy"
              >
                Research
              </button>
            </div>
          )}
        </>
      );
    },
    [
      subscriptionsLoading,
      subscriptionsError,
      subscriptionsStale,
      lastSuccessfulRefreshAt,
      subscriptions,
      strategyPerformance,
      selectedAgentId,
      isAgentModalOpen,
      filteredWatchlist,
      selectedSymbol,
      symbolSearch,
      appliedStrategyName,
      tradeFreshness,
      staleDomains,
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
                  <div className="widget-header-actions">
                    <button
                      type="button"
                      className="widget-debug-button"
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={handleRunTest}
                      title="Re-run strategy (debug)"
                      aria-label="Re-run strategy"
                    >
                      ↺
                    </button>
                    <button
                      type="button"
                      className="widget-gear-button"
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={() => setShowTradeManager(true)}
                      title="Configure strategy"
                      aria-label="Configure strategy"
                    >
                      ⚙
                    </button>
                  </div>
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
          <h1 className="app-title">ODIN Market Workspace v1.35</h1>
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
            <button
              type="button"
              className="tool-button"
              onClick={() => {
                setExportError(null);
                setIsExportModalOpen(true);
              }}
              title="Export candles + overlays CSV"
            >
              Export CSV
            </button>
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
                </div>
                <ChartTimeControl
                  selectedInterval={intervalInput}
                  selectedTimeframe={selectedTimeframe}
                  onIntervalChange={setIntervalInput}
                  onTimeframeChange={setSelectedTimeframe}
                />
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
                overlaySchemas={overlaySchemas}
                overlayLineColors={overlayLineColors}
                overlaySubgraphForced={overlaySubgraphForced}
                overlayAreaStyles={overlayAreaStyles}
                tradeMarkers={tradeMarkers}
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

      {isExportModalOpen && (
        <div className="export-modal-overlay" role="presentation">
          <div className="export-modal" role="dialog" aria-modal="true" aria-label="Export CSV">
            <h3>Export CSV</h3>
            <p className="export-modal-subtitle">Export candles and agent overlays for a date range.</p>
            <div className="export-modal-grid">
              <label>
                <span>Start date</span>
                <input
                  type="date"
                  value={exportStartDate}
                  onChange={(event) => setExportStartDate(event.target.value)}
                />
              </label>
              <label>
                <span>End date</span>
                <input
                  type="date"
                  value={exportEndDate}
                  max={todayDateString}
                  onChange={(event) => setExportEndDate(event.target.value)}
                />
              </label>
            </div>
            {exportJobStatus && (
              <p className="export-modal-status">
                {exportJobStatus.status.toUpperCase()} · Chunks {exportJobStatus.completed_chunks}/
                {Math.max(1, exportJobStatus.total_chunks || 0)}
              </p>
            )}
            {lastCompletedExportJobId && !exportJobId && (
              <p className="export-modal-status">Export complete. Your download should have started automatically.</p>
            )}
            {exportError && <p className="export-modal-error">{exportError}</p>}
            <div className="export-modal-actions">
              <button
                type="button"
                className="tool-button"
                onClick={() => {
                  setIsExportModalOpen(false);
                  setExportError(null);
                }}
              >
                Close
              </button>
              {lastCompletedExportJobId && !exportJobId && (
                <button type="button" className="tool-button" onClick={handleDownloadCompletedExport}>
                  Download Again
                </button>
              )}
              <button
                type="button"
                className="tool-button"
                onClick={() => void handleStartCsvExport()}
                disabled={isExportSubmitting || Boolean(exportJobId) || !exportStartDate || !exportEndDate}
              >
                {isExportSubmitting ? "Starting…" : exportJobId ? "Running…" : "Start Export"}
              </button>
            </div>
          </div>
        </div>
      )}

      <AgentConfigModal
        isOpen={isAgentModalOpen}
        subscription={modalAgent}
        sourceInterval={intervalInput}
        onUpdate={async (id, update) => {
          // Update the subscription with new config
          await updateSubscription(id, { name: update.name, config: update.config });
          
          // If a strategy is currently applied, invalidate trade markers and performance,
          // then re-apply the strategy to get fresh results based on new indicator values
          if (appliedStrategyName) {
            console.log("[Indicator Update] Invalidating trades and re-applying strategy", { appliedStrategyName });
            markTradeSyncing();
            
            try {
              const sessionId = getSessionId();
              console.log("[Indicator Update] Re-applying strategy:", appliedStrategyName);
              const applied = await applyTradeStrategy(sessionId, { strategy_name: appliedStrategyName });
              setTradeMarkers(applied.markers || []);
              setTradePerformance(applied.performance || null);
              markTradeFresh();
              console.log("[Indicator Update] Strategy re-applied successfully", {
                markerCount: applied.marker_count,
                hasPerformance: Boolean(applied.performance),
              });
            } catch (err) {
              console.error("[Indicator Update] Failed to re-apply strategy:", err);
            }
          }
        }}
        onDelete={async (id) => {
          await deleteSubscription(id);
          setOverlayData((current) => {
            const next = new Map(current);
            for (const key of Array.from(next.keys())) {
              if (key.startsWith(`${id}::`)) {
                next.delete(key);
              }
            }
            return next;
          });
          setOverlaySchemas((current) => {
            const next = new Map(current);
            for (const key of Array.from(next.keys())) {
              if (key.startsWith(`${id}::`)) {
                next.delete(key);
              }
            }
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

      {showTradesModal && (
        <TradesModal
          strategyName={appliedStrategyName}
          markers={tradeMarkers}
          candles={strategyCandlesById}
          performance={tradePerformance}
          onClose={() => setShowTradesModal(false)}
        />
      )}

      {showTradeManager && (
        <TradeManagerModal
          sessionId={getSessionId()}
          appliedStrategyName={appliedStrategyName}
          onApply={(result: ApplyTradeStrategyResponse) => {
            console.log("[TradeManager] Manual apply result received in App", {
              strategyName: result.strategy_name,
              markerCount: result.marker_count,
              hasPerformance: Boolean(result.performance),
              performance: result.performance
                ? {
                    totalPl: result.performance.total_pl,
                    totalTrades: result.performance.total_trades,
                    winRate: result.performance.win_rate,
                    maxDrawdown: result.performance.max_drawdown,
                    sharpe: result.performance.sharpe_ratio,
                    curvePoints: result.performance.equity_curve?.length || 0,
                  }
                : null,
            });
            setTradeMarkers(result.markers || []);
            setTradePerformance(result.performance || null);
            setAppliedStrategyName(result.strategy_name || null);
          }}
          onClose={() => setShowTradeManager(false)}
        />
      )}

      {showResearchModal && (
        <ResearchModal
          sessionId={getSessionId()}
          onEvaluated={applyResearchOverlay}
          onClear={clearResearchOverlays}
          initialExpression={researchDraftExpression}
          onExpressionChange={setResearchDraftExpression}
          initialOutputSchema={"line"}
          onClose={() => {
            setShowResearchModal(false);
          }}
        />
      )}
    </div>
  );
}
