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
}

export interface CreateSubscriptionPayload {
  name: string;
  agent_type: string;
  agent_url?: string;
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

  const createSubscription = useCallback(
    async (payload: CreateSubscriptionPayload): Promise<AgentSubscription> => {
      // TODO: Implement agent creation via backend API
      // For now, agents are loaded from overlay_agents.yaml
      console.warn("Agent creation not yet implemented - edit overlay_agents.yaml manually");
      throw new Error("Agent creation not yet implemented. Edit overlay_agents.yaml manually and restart backend.");
    },
    []
  );

  const updateSubscription = useCallback(
    async (id: string, payload: UpdateSubscriptionPayload): Promise<AgentSubscription> => {
      // TODO: Implement agent updates via backend API
      // For now, agents are loaded from overlay_agents.yaml
      console.warn("Agent updates not yet implemented - edit overlay_agents.yaml manually");
      throw new Error("Agent updates not yet implemented. Edit overlay_agents.yaml manually and restart backend.");
    },
    []
  );

  const deleteSubscription = useCallback(
    async (id: string): Promise<void> => {
      // TODO: Implement agent deletion via backend API
      // For now, agents are loaded from overlay_agents.yaml
      console.warn("Agent deletion not yet implemented - edit overlay_agents.yaml manually");
      throw new Error("Agent deletion not yet implemented. Edit overlay_agents.yaml manually and restart backend.");
    },
    []
  );

  return {
    subscriptions,
    loading,
    error,
    refresh,
    updateAgentStatus,
    createSubscription,
    updateSubscription,
    deleteSubscription,
  };
}
