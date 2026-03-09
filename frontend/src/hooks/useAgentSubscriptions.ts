/**
 * React hook for managing agent subscriptions.
 */

import { useEffect, useState, useCallback } from "react";

const BACKEND_URL = "http://localhost:8001";

export interface AgentSubscription {
  id: string;
  name: string;
  agent_type: string;
  agent_url?: string;
  output_schema?: string;
  symbol?: string;
  interval?: string;
  enabled: boolean;
  status: "online" | "offline" | "error" | "connecting";
  last_activity_ts?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
  config?: Record<string, any>;
  candle_type?: string;
  spec_version?: string;
  agent_version?: string;
  description?: string;
  outputs?: Array<{ output_id: string; schema: string; label: string; is_primary: boolean }>;
  indicators?: Array<{ indicator_id: string; name: string; description: string; params_schema?: Record<string, any>; outputs?: any[] }>;
}

export interface DiscoveredAgentMetadata {
  spec_version: string;
  agent_id: string;
  agent_name: string;
  agent_version: string;
  description: string;
  agent_type: "price" | "indicator" | "event";
  outputs: Array<{ output_id: string; schema: string; label: string; is_primary: boolean }>;
  indicators?: Array<{ indicator_id: string; name: string; description: string; params_schema?: Record<string, any>; outputs?: any[] }>;
}

export interface DiscoverAgentResult {
  agent_url: string;
  metadata: DiscoveredAgentMetadata;
  discovered_at: string;
}

export interface CreateSubscriptionPayload {
  name: string;
  agent_type: string;
  agent_url?: string;
  indicator_id?: string;
  output_schema?: string;
  symbol?: string;
  interval?: string;
  enabled?: boolean;
  config?: Record<string, any>;
  candle_type?: string;
  [key: string]: any;
}

export interface UpdateSubscriptionPayload {
  name?: string;
  agent_url?: string;
  output_schema?: string;
  symbol?: string;
  interval?: string;
  enabled?: boolean;
  config?: Record<string, any>;
  candle_type?: string;
  [key: string]: any;
}

export interface UseAgentSubscriptionsResult {
  subscriptions: AgentSubscription[] | null;
  loading: boolean;
  error: string | null;
  refresh: (silent?: boolean) => Promise<void>;
  updateAgentStatus: (agentId: string, status: "online" | "offline" | "error" | "connecting", errorMessage?: string | null) => void;
  createSubscription: (payload: CreateSubscriptionPayload) => Promise<AgentSubscription>;
  discoverAgent: (agentUrl: string) => Promise<DiscoverAgentResult>;
  updateSubscription: (id: string, payload: UpdateSubscriptionPayload) => Promise<AgentSubscription>;
  deleteSubscription: (id: string) => Promise<void>;
}

export function useAgentSubscriptions(): UseAgentSubscriptionsResult {
  const [subscriptions, setSubscriptions] = useState<AgentSubscription[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cachedSubscriptions, setCachedSubscriptions] = useState<AgentSubscription[] | null>(null);

  const refresh = useCallback(async (silent: boolean = false) => {
    try {
      // Skip loading state for silent background refreshes to prevent UI flicker
      if (!silent) {
        setLoading(true);
      }
      setError(null);
      
      // Fetch from backend API
      const response = await fetch(`${BACKEND_URL}/api/agents`);
      if (!response.ok) {
        throw new Error(`Failed to fetch agents: ${response.statusText}`);
      }
      
      const data = await response.json();
      const agents = data.agents || [];
      
      setSubscriptions(agents);
      setCachedSubscriptions(agents); // Cache successful response
      console.log(`✅ Loaded ${agents.length} agent(s) from backend`);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error";
      console.error("Failed to fetch subscriptions:", err);
      
      // Use the cached data from state if available
      setCachedSubscriptions((prev) => {
        if (prev && prev.length > 0) {
          console.log(`📦 Using cached agents (${prev.length})`);
          setSubscriptions(prev);
        } else {
          setError(errorMessage);
          setSubscriptions([]);
        }
        return prev;
      });
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, []);

  // Load subscriptions on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll backend status in background to keep agent online/offline state current.
  useEffect(() => {
    const intervalId = setInterval(() => {
      void refresh(true);
    }, 5000);

    return () => clearInterval(intervalId);
  }, [refresh]);

  const updateAgentStatus = useCallback((agentId: string, status: "online" | "offline" | "error" | "connecting", errorMessage?: string | null) => {
    setSubscriptions((prev) => {
      if (!prev) return prev;
      return prev.map((agent) => {
        if (agent.id === agentId) {
          return {
            ...agent,
            status,
            error_message: errorMessage || null,
            last_activity_ts: new Date().toISOString(),
          };
        }
        return agent;
      });
    });
  }, []);

  const discoverAgent = useCallback(async (agentUrl: string): Promise<DiscoverAgentResult> => {
    const response = await fetch(`${BACKEND_URL}/api/agents/discover`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ agent_url: agentUrl }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Discover failed: ${response.statusText}`);
    }

    return response.json();
  }, []);

  const createSubscription = useCallback(
    async (payload: CreateSubscriptionPayload): Promise<AgentSubscription> => {
      const response = await fetch(`${BACKEND_URL}/api/agents`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          agent_url: payload.agent_url,
          indicator_id: payload.indicator_id,
          params: payload.config || {},
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Create failed: ${response.statusText}`);
      }

      const data = await response.json();
      await refresh(true);
      return data.agent as AgentSubscription;
    },
    [refresh]
  );

  const updateSubscription = useCallback(
    async (id: string, payload: UpdateSubscriptionPayload): Promise<AgentSubscription> => {
      const response = await fetch(`${BACKEND_URL}/api/agents/${id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          agent_name: payload.name,
          params: payload.config || {},
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Update failed: ${response.statusText}`);
      }

      const data = await response.json();
      await refresh(true);
      return data.agent as AgentSubscription;
    },
    [refresh]
  );

  const deleteSubscription = useCallback(
    async (id: string): Promise<void> => {
      const response = await fetch(`${BACKEND_URL}/api/agents/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Delete failed: ${response.statusText}`);
      }

      await refresh(true);
    },
    [refresh]
  );

  return {
    subscriptions,
    loading,
    error,
    refresh,
    updateAgentStatus,
    createSubscription,
    discoverAgent,
    updateSubscription,
    deleteSubscription,
  };
}
