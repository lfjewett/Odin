const BACKEND_URL = "http://localhost:8001";

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

export async function getSessionVariables(sessionId: string): Promise<SessionVariablesResponse> {
  const response = await fetch(`${BACKEND_URL}/api/sessions/${encodeURIComponent(sessionId)}/variables`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to get session variables: ${response.statusText}`);
  }
  return response.json();
}
