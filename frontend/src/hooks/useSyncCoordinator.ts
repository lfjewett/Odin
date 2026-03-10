import { useCallback, useMemo, useState } from "react";

export type SyncDomain = "agent" | "overlay" | "trade" | "workspace";

export interface DomainRevisions {
  agent: number;
  overlay: number;
  trade: number;
  workspace: number;
}

export interface StateEventMessage {
  type: "state_event";
  event_name: string;
  domain: SyncDomain;
  revision: number;
  session_id?: string;
  emitted_at: string;
  payload?: Record<string, unknown>;
  server_revisions?: Partial<DomainRevisions>;
}

export interface SyncSnapshotMessage {
  type: "sync_snapshot";
  emitted_at: string;
  stale_domains: SyncDomain[];
  server_revisions: DomainRevisions;
  trade_sessions?: Array<{
    session_id: string;
    result: Record<string, unknown>;
  }>;
}

const INITIAL_REVISIONS: DomainRevisions = {
  agent: 0,
  overlay: 0,
  trade: 0,
  workspace: 0,
};

export function useSyncCoordinator() {
  const [revisions, setRevisions] = useState<DomainRevisions>(INITIAL_REVISIONS);
  const [staleDomains, setStaleDomains] = useState<SyncDomain[]>([]);
  const [tradeFreshness, setTradeFreshness] = useState<"fresh" | "syncing" | "stale">("fresh");
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);

  const updateFromServerRevisions = useCallback((incoming?: Partial<DomainRevisions>) => {
    if (!incoming) {
      return;
    }
    setRevisions((prev) => ({
      agent: Math.max(prev.agent, Number(incoming.agent || 0)),
      overlay: Math.max(prev.overlay, Number(incoming.overlay || 0)),
      trade: Math.max(prev.trade, Number(incoming.trade || 0)),
      workspace: Math.max(prev.workspace, Number(incoming.workspace || 0)),
    }));
  }, []);

  const onStateEvent = useCallback((event: StateEventMessage) => {
    setLastEventAt(event.emitted_at || new Date().toISOString());
    setRevisions((prev) => ({
      ...prev,
      [event.domain]: Math.max(prev[event.domain], Number(event.revision || 0)),
    }));
    updateFromServerRevisions(event.server_revisions);

    if (event.event_name === "trade.results.invalidated") {
      setTradeFreshness("syncing");
    } else if (event.event_name === "trade.results.recomputed") {
      setTradeFreshness("fresh");
      setStaleDomains((prev) => prev.filter((domain) => domain !== "trade"));
    }
  }, [updateFromServerRevisions]);

  const onSyncSnapshot = useCallback((snapshot: SyncSnapshotMessage) => {
    setLastEventAt(snapshot.emitted_at || new Date().toISOString());
    setStaleDomains(snapshot.stale_domains || []);
    setRevisions(snapshot.server_revisions || INITIAL_REVISIONS);
    if ((snapshot.stale_domains || []).includes("trade")) {
      setTradeFreshness("stale");
    }
  }, []);

  const markTradeFresh = useCallback(() => setTradeFreshness("fresh"), []);
  const markTradeSyncing = useCallback(() => setTradeFreshness("syncing"), []);

  const clientRevisions = useMemo(() => revisions, [revisions]);

  return {
    revisions,
    staleDomains,
    tradeFreshness,
    lastEventAt,
    clientRevisions,
    onStateEvent,
    onSyncSnapshot,
    markTradeFresh,
    markTradeSyncing,
  };
}
