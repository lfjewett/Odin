const BACKEND_URL = "";

export interface WorkspaceSummary {
  name: string;
  schema_version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceState {
  selectedSymbol: string;
  intervalInput: string;
  selectedTimeframe: number;
  isLeftCollapsed: boolean;
  isRightCollapsed: boolean;
  widgets: Array<{
    id: "watchlist" | "overlayAgents" | "tradingBots2";
    side: "left" | "right";
    height: number;
  }>;
  selectedAgentId: string | null;
  agentSubscriptions: Array<{
    id: string;
    name: string;
    agent_type: string;
    agent_url?: string;
    config: Record<string, unknown>;
  }>;
  appliedStrategyName?: string | null;
  tradeMarkers?: TradeMarker[];
  tradePerformance?: TradePerformance | null;
}

export interface WorkspaceRecord {
  name: string;
  schema_version: number;
  state: WorkspaceState;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceSummary[];
  active_workspace: string | null;
}

export async function listWorkspaces(): Promise<WorkspaceListResponse> {
  const response = await fetch(`${BACKEND_URL}/api/workspaces`);
  if (!response.ok) {
    throw new Error(`Failed to list workspaces: ${response.statusText}`);
  }
  return response.json();
}

export async function getWorkspace(name: string): Promise<WorkspaceRecord> {
  const response = await fetch(`${BACKEND_URL}/api/workspaces/${encodeURIComponent(name)}`);
  if (!response.ok) {
    throw new Error(`Failed to get workspace: ${response.statusText}`);
  }
  return response.json();
}

export async function saveWorkspace(
  name: string,
  state: WorkspaceState,
  schemaVersion: number = 1
): Promise<WorkspaceRecord> {
  const response = await fetch(`${BACKEND_URL}/api/workspaces/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      schema_version: schemaVersion,
      state,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to save workspace: ${response.statusText}`);
  }

  return response.json();
}

export async function activateWorkspace(name: string): Promise<WorkspaceRecord> {
  const response = await fetch(`${BACKEND_URL}/api/workspaces/${encodeURIComponent(name)}/activate`, {
    method: "POST",
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to activate workspace: ${response.statusText}`);
  }

  const data = await response.json();
  return data.workspace as WorkspaceRecord;
}

export async function deleteWorkspace(name: string): Promise<{ deleted: string; active_workspace: string | null }> {
  const response = await fetch(`${BACKEND_URL}/api/workspaces/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to delete workspace: ${response.statusText}`);
  }

  return response.json();
}

export interface Variable {
  name: string;
  type: "ohlcv" | "indicator";
  schema: string;
  agent_id: string | null;
  output_id: string | null;
}

export interface SessionVariablesResponse {
  session_id: string;
  variables: Variable[];
  count: number;
}

export interface TradeStrategyRecord {
  session_id: string;
  name: string;
  description: string;
  long_entry_rules: string[];
  long_exit_rules: string[];
  short_entry_rules: string[];
  short_exit_rules: string[];
  created_at: string;
  updated_at: string;
}

export interface TradeStrategiesResponse {
  session_id: string;
  strategies: TradeStrategyRecord[];
  count: number;
}

export interface TradeStrategyValidationResponse {
  session_id: string;
  valid: boolean;
  errors: string[];
}

export interface TradeMarker {
  id: string;
  candle_id?: string;
  ts: string;
  title: string;
  description?: string;
  severity?: "info" | "warning" | "critical";
  action?: "ENTRY" | "EXIT" | "LONG_ENTRY" | "LONG_EXIT" | "SHORT_ENTRY" | "SHORT_EXIT";
}

export interface TradePerformancePoint {
  ts: string;
  equity: number;
}

export interface TradePerformance {
  starting_capital: number;
  lot_size: number;
  final_equity: number;
  total_pl: number;
  total_trades: number;
  win_rate: number;
  max_drawdown: number;
  sharpe_ratio: number;
  average_win: number;
  average_loss: number;
  max_loss: number;
  equity_curve: TradePerformancePoint[];
}

export interface ApplyTradeStrategyResponse {
  session_id: string;
  strategy_name: string;
  long_entry_rule: string;
  long_exit_rules: string[];
  short_entry_rule: string;
  short_exit_rules: string[];
  markers: TradeMarker[];
  marker_count: number;
  performance?: TradePerformance;
}

export type ResearchOutputSchema = "line" | "area" | "histogram";

export interface ResearchRecord {
  id: string;
  ts: string;
  value?: number;
  upper?: number;
  lower?: number;
  metadata?: Record<string, unknown>;
}

export interface EvaluateResearchExpressionResponse {
  session_id: string;
  expression: string;
  schema: ResearchOutputSchema;
  records: ResearchRecord[];
  count: number;
}

export interface SessionViewportResponse {
  session_id: string;
  bars: Array<{
    id: string;
    seq?: number;
    rev: number;
    bar_state: "partial" | "provisional_close" | "session_reconciled" | "final";
    ts: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  overlays: ResearchRecord[];
  trade_markers: TradeMarker[];
  count: number;
  overlay_count: number;
  trade_marker_count: number;
  range_start_ts: string | null;
  range_end_ts: string | null;
  viewport_from_ts: string | null;
  viewport_to_ts: string | null;
  total_bars: number;
  viewport_days: number;
  is_viewported: boolean;
  is_latest: boolean;
  follow_live: boolean;
  has_previous: boolean;
  has_next: boolean;
  slider_value: number;
}

export interface CreateCsvExportPayload {
  start_date: string;
  end_date: string;
  interval?: string;
  settle_min_delay_seconds?: number;
  settle_poll_seconds?: number;
  settle_timeout_seconds?: number;
}

export interface CreateCsvExportResponse {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  session_id: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
}

export interface CsvExportJobStatus {
  job_id: string;
  session_id: string;
  symbol: string;
  interval: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  start_date: string;
  end_date: string;
  total_chunks: number;
  completed_chunks: number;
  current_chunk: number;
  chunk_window: { from: string; to: string } | null;
  candle_count: number;
  overlay_count: number;
  ready: boolean;
}

export async function getSessionVariables(sessionId: string): Promise<SessionVariablesResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/variables`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to get session variables: ${response.statusText}`);
  }
  return response.json();
}

export async function listTradeStrategies(sessionId: string): Promise<TradeStrategiesResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to list trade strategies: ${response.statusText}`);
  }
  return response.json();
}

export async function getTradeStrategy(sessionId: string, strategyName: string): Promise<TradeStrategyRecord> {
  const response = await fetch(
    `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies/${encodeURIComponent(strategyName)}`
  );
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to get trade strategy: ${response.statusText}`);
  }
  return response.json();
}

export async function saveTradeStrategy(
  sessionId: string,
  strategyName: string,
  payload: {
    description?: string;
    long_entry_rules: string[];
    long_exit_rules: string[];
    short_entry_rules: string[];
    short_exit_rules: string[];
  }
): Promise<TradeStrategyRecord> {
  const response = await fetch(
    `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies/${encodeURIComponent(strategyName)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail = typeof errorData.detail === "string" ? errorData.detail : JSON.stringify(errorData.detail);
    throw new Error(detail || `Failed to save trade strategy: ${response.statusText}`);
  }

  return response.json();
}

export async function deleteTradeStrategy(sessionId: string, strategyName: string): Promise<void> {
  const response = await fetch(
    `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies/${encodeURIComponent(strategyName)}`,
    {
      method: "DELETE",
    }
  );

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to delete trade strategy: ${response.statusText}`);
  }
}

export async function validateTradeStrategy(
  sessionId: string,
  payload: {
    long_entry_rules: string[];
    long_exit_rules: string[];
    short_entry_rules: string[];
    short_exit_rules: string[];
  }
): Promise<TradeStrategyValidationResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies/validate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to validate trade strategy: ${response.statusText}`);
  }

  return response.json();
}

export async function applyTradeStrategy(
  sessionId: string,
  payload: {
    strategy_name?: string;
    long_entry_rules: string[];
    long_exit_rules: string[];
    short_entry_rules: string[];
    short_exit_rules: string[];
  }
): Promise<ApplyTradeStrategyResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/trade-strategies/apply`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to apply trade strategy: ${response.statusText}`);
  }

  return response.json();
}

export async function evaluateResearchExpression(
  sessionId: string,
  payload: {
    expression: string;
    output_schema: ResearchOutputSchema;
  }
): Promise<EvaluateResearchExpressionResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/research/evaluate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to evaluate research expression: ${response.statusText}`);
  }

  return response.json();
}

export async function createCsvExportJob(
  sessionId: string,
  payload: CreateCsvExportPayload
): Promise<CreateCsvExportResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/exports/csv`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create CSV export job: ${response.statusText}`);
  }

  return response.json();
}

export async function getCsvExportJobStatus(sessionId: string, jobId: string): Promise<CsvExportJobStatus> {
  const response = await fetch(
    `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/exports/csv/${encodeURIComponent(jobId)}`
  );

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch CSV export job status: ${response.statusText}`);
  }

  return response.json();
}

export function getCsvExportDownloadUrl(sessionId: string, jobId: string): string {
  return `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/exports/csv/${encodeURIComponent(jobId)}/download`;
}

export async function fetchSessionViewport(
  sessionId: string,
  params: {
    fromTs?: string;
    toTs?: string;
    windowDays?: number;
  } = {}
): Promise<SessionViewportResponse> {
  const search = new URLSearchParams();
  if (params.fromTs) search.set("from_ts", params.fromTs);
  if (params.toTs) search.set("to_ts", params.toTs);
  if (typeof params.windowDays === "number" && Number.isFinite(params.windowDays)) {
    search.set("window_days", String(params.windowDays));
  }

  const query = search.toString();
  const response = await fetch(
    `${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/viewport${query ? `?${query}` : ""}`
  );

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch viewport: ${response.statusText}`);
  }

  return response.json();
}
