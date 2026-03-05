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
  onClose,
}) => {
  const [elapsed, setElapsed] = useState<string>("—");

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

  if (!isOpen || !subscription) return null;

  const statusEmoji = getStatusEmoji(subscription.status);
  const statusColor = getStatusColor(subscription.status);

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
