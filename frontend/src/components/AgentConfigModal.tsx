/**
 * AgentConfigModal - Read-only agent configuration viewer
 * Displays all agent configuration and status in a clean, wide modal.
 */

import React, { useState, useEffect } from "react";
import { AgentSubscription } from "../hooks/useAgentSubscriptions";
import "./AgentConfigModal.css";

export interface AgentConfigModalProps {
  isOpen: boolean;
  subscription?: AgentSubscription | null;
  onUpdate?: (id: string, update: { name?: string; config: Record<string, unknown> }) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
  onClose: () => void;
}

type StatusColor = "online" | "offline" | "error" | "connecting";

const getStatusEmoji = (status: string): string => {
  switch (status) {
    case "online":
      return "🟢";
    case "offline":
      return "🔴";
    case "error":
      return "🟠";
    case "connecting":
      return "🟡";
    default:
      return "⚪";
  }
};

const getStatusColor = (status: string): StatusColor => {
  return (status as StatusColor) || "offline";
};

export const AgentConfigModal: React.FC<AgentConfigModalProps> = ({
  isOpen,
  subscription,
  onUpdate,
  onDelete,
  onClose,
}) => {
  const [elapsed, setElapsed] = useState<string>("—");
  const [agentNameInput, setAgentNameInput] = useState<string>("");
  const [periodInput, setPeriodInput] = useState<string>("");
  const [lineColorInput, setLineColorInput] = useState<string>("#cbd5e1");
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Update elapsed time every second
  useEffect(() => {
    if (!isOpen || !subscription?.created_at) return;

    const formatElapsed = (createdIso: string): string => {
      const created = new Date(createdIso).getTime();
      if (Number.isNaN(created)) return "—";
      const totalSecs = Math.max(0, Math.floor((Date.now() - created) / 1000));
      const hours = Math.floor(totalSecs / 3600);
      const mins = Math.floor((totalSecs % 3600) / 60);
      const secs = totalSecs % 60;
      
      if (hours > 0) return `${hours}h ${mins}m ${secs}s`;
      if (mins > 0) return `${mins}m ${secs}s`;
      return `${secs}s`;
    };

    setElapsed(formatElapsed(subscription.created_at));
    const interval = setInterval(() => {
      setElapsed(formatElapsed(subscription.created_at));
    }, 1000);

    return () => clearInterval(interval);
  }, [isOpen, subscription?.created_at]);

  useEffect(() => {
    if (!isOpen || !subscription) {
      return;
    }

    const nextPeriod = subscription.config?.period;
    const nextColor = subscription.config?.line_color;

    setAgentNameInput(subscription.name || "");
    setPeriodInput(nextPeriod !== undefined && nextPeriod !== null ? String(nextPeriod) : "20");
    setLineColorInput(typeof nextColor === "string" && nextColor.length > 0 ? nextColor : "#cbd5e1");
    setActionError(null);
    setSaving(false);
    setDeleting(false);
  }, [isOpen, subscription?.id]);

  if (!isOpen || !subscription) return null;

  const statusEmoji = getStatusEmoji(subscription.status);
  const statusColor = getStatusColor(subscription.status);
  const isIndicator = subscription.agent_type === "indicator";

  const handleSave = async () => {
    if (!subscription || !onUpdate) {
      return;
    }

    const parsedPeriod = Number.parseInt(periodInput, 10);
    if (!Number.isFinite(parsedPeriod) || parsedPeriod < 1) {
      setActionError("Period must be an integer greater than or equal to 1.");
      return;
    }

    setSaving(true);
    setActionError(null);
    try {
      await onUpdate(subscription.id, {
        name: agentNameInput.trim() || subscription.name,
        config: {
          ...subscription.config,
          period: parsedPeriod,
          line_color: lineColorInput,
        },
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to save indicator settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!subscription || !onDelete) {
      return;
    }

    setDeleting(true);
    setActionError(null);
    try {
      await onDelete(subscription.id);
      onClose();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to remove indicator.");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="agent-modal-overlay" onClick={onClose}>
      <div className="agent-modal-content" onClick={(e) => e.stopPropagation()}>
        {/* Header - Agent Name and Status */}
        <div className="agent-modal-header">
          <div className="agent-header-top">
            <div className="agent-title-section">
              <h1 className="agent-name">{subscription.name}</h1>
              <span className={`agent-status-badge status-${statusColor}`}>
                {statusEmoji} {subscription.status.charAt(0).toUpperCase() + subscription.status.slice(1)}
              </span>
            </div>
            <button className="agent-modal-close" onClick={onClose}>×</button>
          </div>
          
          <div className="agent-description">
            {subscription.description ? (
              <p>{subscription.description}</p>
            ) : (
              <p className="no-description">No description provided</p>
            )}
          </div>
        </div>

        {/* Body */}
        <div className="agent-modal-body">
          {/* Agent Metadata Section */}
          <section className="agent-section">
            <h2 className="section-title">Agent Information</h2>
            <div className="info-grid">
              <div className="info-item">
                <span className="info-label">Agent ID</span>
                <span className="info-value font-mono">{subscription.id}</span>
              </div>
              <div className="info-item">
                <span className="info-label">URL</span>
                <span className="info-value font-mono">{subscription.agent_url}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Type</span>
                <span className="info-value">{subscription.agent_type}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Spec Version</span>
                <span className="info-value">{subscription.spec_version || "—"}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Agent Version</span>
                <span className="info-value">{subscription.agent_version || "—"}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Output Schema</span>
                <span className="info-value">{subscription.output_schema || "—"}</span>
              </div>
            </div>
          </section>

          {/* Status Section */}
          <section className="agent-section">
            <h2 className="section-title">Status</h2>
            <div className="status-grid">
              <div className="status-item">
                <span className="status-label">Health</span>
                <span className={`status-badge status-${statusColor}`}>
                  {statusEmoji} {subscription.status}
                </span>
              </div>
              <div className="status-item">
                <span className="status-label">Last Activity</span>
                <span className="status-value">
                  {subscription.last_activity_ts
                    ? new Date(subscription.last_activity_ts).toLocaleString()
                    : "Never"}
                </span>
              </div>
              <div className="status-item">
                <span className="status-label">Created</span>
                <span className="status-value">
                  {new Date(subscription.created_at).toLocaleString()}
                </span>
              </div>
              <div className="status-item">
                <span className="status-label">Time Since Created</span>
                <span className="status-value">{elapsed}</span>
              </div>
              {subscription.error_message && (
                <div className="status-item status-error-row">
                  <span className="status-label">Error Message</span>
                  <span className="error-message">{subscription.error_message}</span>
                </div>
              )}
            </div>
          </section>

          {/* Configuration Section */}
          {subscription.config && Object.keys(subscription.config).length > 0 && (
            <section className="agent-section">
              <h2 className="section-title">Configuration</h2>
              <div className="config-grid">
                {Object.entries(subscription.config).map(([key, value]) => (
                  <div key={key} className="config-item">
                    <span className="config-label">
                      {key
                        .replace(/_/g, " ")
                        .split(" ")
                        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                        .join(" ")}
                    </span>
                    <span className="config-value">
                      {typeof value === "boolean"
                        ? value
                          ? "✓ Enabled"
                          : "✗ Disabled"
                        : typeof value === "string" || typeof value === "number"
                        ? String(value)
                        : JSON.stringify(value)}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {isIndicator && (
            <section className="agent-section">
              <h2 className="section-title">Indicator Controls</h2>
              <div className="config-grid">
                <div className="config-item">
                  <span className="config-label">Agent Name</span>
                  <input
                    className="agent-input"
                    type="text"
                    value={agentNameInput}
                    onChange={(event) => setAgentNameInput(event.target.value)}
                    disabled={saving || deleting}
                  />
                </div>
                <div className="config-item">
                  <span className="config-label">Period</span>
                  <input
                    className="agent-input"
                    type="number"
                    min={1}
                    step={1}
                    value={periodInput}
                    onChange={(event) => setPeriodInput(event.target.value)}
                    disabled={saving || deleting}
                  />
                </div>
                <div className="config-item">
                  <span className="config-label">Line Color</span>
                  <input
                    className="agent-input agent-input-color"
                    type="color"
                    value={lineColorInput}
                    onChange={(event) => setLineColorInput(event.target.value)}
                    disabled={saving || deleting}
                  />
                </div>
              </div>
              <div className="agent-actions-row">
                <button className="btn-save" onClick={handleSave} disabled={saving || deleting}>
                  {saving ? "Saving..." : "Save Settings"}
                </button>
                <button className="btn-delete" onClick={handleDelete} disabled={saving || deleting}>
                  {deleting ? "Removing..." : "Remove Indicator"}
                </button>
              </div>
              {actionError && <p className="agent-action-error">{actionError}</p>}
            </section>
          )}

          {/* No Configuration */}
          {!subscription.config ||
            (Object.keys(subscription.config).length === 0 && (
              <section className="agent-section">
                <h2 className="section-title">Configuration</h2>
                <p className="no-config">No configuration options for this agent.</p>
              </section>
            ))}
        </div>

        {/* Footer */}
        <div className="agent-modal-footer">
          <button className="btn-close" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentConfigModal;
