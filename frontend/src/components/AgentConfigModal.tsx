/**
 * AgentConfigModal - Agent configuration viewer/editor
 * Displays agent configuration and status in a clean, wide modal.
 */

import React, { useEffect, useMemo, useState } from "react";
import { AgentSubscription } from "../hooks/useAgentSubscriptions";
import "./AgentConfigModal.css";

export interface AgentConfigModalProps {
  isOpen: boolean;
  subscription?: AgentSubscription | null;
  sourceInterval?: string;
  onUpdate?: (id: string, update: { name?: string; config: Record<string, unknown> }) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
  onClose: () => void;
}

type StatusColor = "online" | "offline" | "error" | "connecting";
type EditableFieldType = "integer" | "number" | "boolean" | "string";

type IndicatorDefinition = NonNullable<AgentSubscription["indicators"]>[number];

interface EditableConfigField {
  key: string;
  label: string;
  type: EditableFieldType;
  required: boolean;
  min?: number;
  max?: number;
  enumOptions?: string[];
}

const CANONICAL_INTERVALS = [
  "1m",
  "2m",
  "3m",
  "4m",
  "5m",
  "10m",
  "15m",
  "20m",
  "30m",
  "1h",
  "2h",
  "4h",
  "8h",
  "12h",
  "1d",
  "2d",
  "1w",
  "1M",
] as const;

const INTERVAL_RE = /^(\d+)(m|h|d|w|M)$/;

const parseInterval = (interval?: string): { value: number; unit: string } | null => {
  if (!interval) {
    return null;
  }

  const trimmed = interval.trim();
  const match = INTERVAL_RE.exec(trimmed);
  if (!match) {
    return null;
  }

  const value = Number.parseInt(match[1], 10);
  if (!Number.isFinite(value) || value <= 0) {
    return null;
  }

  return { value, unit: match[2] };
};

const validateAggregationInterval = (
  sourceInterval: string | undefined,
  aggregationInterval: string
): string | null => {
  const trimmed = aggregationInterval.trim();
  if (!trimmed) {
    return null;
  }

  if (!CANONICAL_INTERVALS.includes(trimmed as (typeof CANONICAL_INTERVALS)[number])) {
    return "Aggregation interval must be one of the canonical values (e.g. 2m, 5m, 1h).";
  }

  const source = parseInterval(sourceInterval);
  const target = parseInterval(trimmed);

  if (!target) {
    return "Aggregation interval format is invalid.";
  }

  if (!source) {
    return null;
  }

  if (source.unit !== target.unit) {
    return `Aggregation interval must use the same unit as source interval ${sourceInterval}.`;
  }

  if (target.value < source.value) {
    return `Aggregation interval must be greater than or equal to source interval ${sourceInterval}.`;
  }

  if (target.value % source.value !== 0) {
    return `Aggregation interval must be an integer multiple of source interval ${sourceInterval}.`;
  }

  return null;
};

const humanizeConfigKey = (key: string): string =>
  key
    .replace(/_/g, " ")
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");

const normalizeIndicatorInstanceId = (agentId: string): string => agentId.replace(/__\d+$/, "");

const inferFieldType = (value: unknown): EditableFieldType => {
  if (typeof value === "boolean") {
    return "boolean";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? "integer" : "number";
  }
  return "string";
};

const parseFieldValue = (rawValue: string, fieldType: EditableFieldType): unknown => {
  if (fieldType === "integer") {
    return Number.parseInt(rawValue, 10);
  }
  if (fieldType === "number") {
    return Number.parseFloat(rawValue);
  }
  if (fieldType === "boolean") {
    return rawValue === "true";
  }
  return rawValue;
};

const inferIndicatorDefinition = (subscription?: AgentSubscription | null): IndicatorDefinition | null => {
  const indicators = subscription?.indicators || [];
  if (!indicators.length) {
    return null;
  }

  if (subscription?.selected_indicator_id) {
    const matchedBySelectedId = indicators.find(
      (indicator) => indicator.indicator_id === subscription.selected_indicator_id
    );
    if (matchedBySelectedId) {
      return matchedBySelectedId;
    }
  }

  const normalizedAgentId = normalizeIndicatorInstanceId(subscription?.id || "");
  const matchedById = indicators.find((indicator) =>
    normalizedAgentId.endsWith(`__${indicator.indicator_id}`)
  );
  if (matchedById) {
    return matchedById;
  }

  const subscriptionOutputs = JSON.stringify(subscription?.outputs || []);
  const matchedByOutputs = indicators.find(
    (indicator) => JSON.stringify(indicator.outputs || []) === subscriptionOutputs
  );
  if (matchedByOutputs) {
    return matchedByOutputs;
  }

  const configuredKeys = new Set(
    Object.keys(subscription?.config || {}).filter((key) => key !== "line_color")
  );

  let bestMatch: { indicator: IndicatorDefinition; score: number } | null = null;
  indicators.forEach((indicator) => {
    const schema = indicator.params_schema || {};
    const schemaKeys = Object.keys(schema).filter((key) => key !== "line_color");
    if (!schemaKeys.length) {
      return;
    }

    const overlap = schemaKeys.filter((key) => configuredKeys.has(key)).length;
    if (!overlap) {
      return;
    }

    const exactMatch = schemaKeys.length === configuredKeys.size && schemaKeys.every((key) => configuredKeys.has(key));
    const score = overlap * 10 + (exactMatch ? 100 : 0) - Math.abs(schemaKeys.length - configuredKeys.size);

    if (!bestMatch || score > bestMatch.score) {
      bestMatch = { indicator, score };
    }
  });

  if (bestMatch) {
    return bestMatch.indicator;
  }

  return indicators.length === 1 ? indicators[0] : null;
};

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
  sourceInterval,
  onUpdate,
  onDelete,
  onClose,
}) => {
  const [elapsed, setElapsed] = useState<string>("—");
  const [agentNameInput, setAgentNameInput] = useState<string>("");
  const [paramInputs, setParamInputs] = useState<Record<string, string>>({});
  const [aggregationIntervalInput, setAggregationIntervalInput] = useState<string>("");
  const [lineColorInput, setLineColorInput] = useState<string>("#cbd5e1");
  const [areaFillModeInput, setAreaFillModeInput] = useState<"solid" | "conditional">("conditional");
  const [areaFillOpacityInput, setAreaFillOpacityInput] = useState<number>(50);
  const [areaConditionalUpColorInput, setAreaConditionalUpColorInput] = useState<string>("#22c55e");
  const [areaConditionalDownColorInput, setAreaConditionalDownColorInput] = useState<string>("#ef4444");
  const [areaUseSourceStyleInput, setAreaUseSourceStyleInput] = useState<boolean>(true);
  const [areaShowLabelsInput, setAreaShowLabelsInput] = useState<boolean>(true);
  const [visibleInput, setVisibleInput] = useState<boolean>(true);
  const [savedVisibleInput, setSavedVisibleInput] = useState<boolean>(true);
  const [forceSubgraphInput, setForceSubgraphInput] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const inferredIndicator = useMemo(() => inferIndicatorDefinition(subscription), [subscription]);
  const editableFields = useMemo<EditableConfigField[]>(() => {
    if (!subscription) {
      return [];
    }

    const schema = inferredIndicator?.params_schema || {};
    const config = subscription.config || {};
    const schemaKeys = Object.keys(schema).filter(
      (key) => key !== "line_color" && key !== "aggregation_interval"
    );
    const orderedKeys: string[] = schemaKeys.length
      ? schemaKeys
      : Object.keys(config).filter(
          (key) =>
            key !== "line_color" &&
            key !== "aggregation_interval" &&
            key !== "visible" &&
            key !== "force_subgraph" &&
            key !== "area_fill_mode" &&
            key !== "area_fill_opacity" &&
            key !== "area_conditional_up_color" &&
            key !== "area_conditional_down_color" &&
            key !== "area_use_source_style" &&
            key !== "area_show_labels"
        );

    return orderedKeys.map((key) => {
      const definition = schema[key] as Record<string, unknown> | undefined;
      const min = typeof definition?.min === "number" ? definition.min : undefined;
      const max = typeof definition?.max === "number" ? definition.max : undefined;
      const typeFromSchema = definition?.type;
      const enumOptions = Array.isArray(definition?.enum)
        ? definition.enum
            .filter((option) => ["string", "number", "boolean"].includes(typeof option))
            .map((option) => String(option))
        : undefined;

      let type: EditableFieldType = inferFieldType(config[key]);
      if (typeFromSchema === "integer" || typeFromSchema === "number" || typeFromSchema === "boolean") {
        type = typeFromSchema;
      }

      return {
        key,
        label: humanizeConfigKey(key),
        type,
        required: Boolean(definition?.required),
        min,
        max,
        enumOptions,
      };
    });
  }, [inferredIndicator, subscription]);

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
    const nextVisible = subscription.config?.visible !== false;
    const nextColor = subscription.config?.line_color;
    const nextFillMode = subscription.config?.area_fill_mode;
    const nextFillOpacity = Number(subscription.config?.area_fill_opacity);
    const nextConditionalUpColor = subscription.config?.area_conditional_up_color;
    const nextConditionalDownColor = subscription.config?.area_conditional_down_color;
    const nextUseSourceStyle = subscription.config?.area_use_source_style;
    const nextShowLabels = subscription.config?.area_show_labels;
    const nextAggregationInterval = subscription.config?.aggregation_interval;
    const nextParamInputs: Record<string, string> = {};

    editableFields.forEach((field) => {
      const currentValue = subscription.config?.[field.key];
      const schemaDefault = (inferredIndicator?.params_schema?.[field.key] as Record<string, unknown> | undefined)?.default;

      if (currentValue !== undefined && currentValue !== null) {
        nextParamInputs[field.key] = String(currentValue);
      } else if (schemaDefault !== undefined && schemaDefault !== null) {
        nextParamInputs[field.key] = String(schemaDefault);
      } else if (field.enumOptions && field.enumOptions.length > 0) {
        nextParamInputs[field.key] = field.required ? field.enumOptions[0] : "";
      } else if (field.type === "boolean") {
        nextParamInputs[field.key] = "false";
      } else {
        nextParamInputs[field.key] = "";
      }
    });

    setAgentNameInput(subscription.name || "");
    setParamInputs(nextParamInputs);
    setAggregationIntervalInput(typeof nextAggregationInterval === "string" ? nextAggregationInterval : "");
    setSavedVisibleInput(nextVisible);
    setVisibleInput(nextVisible);
    setLineColorInput(typeof nextColor === "string" && nextColor.length > 0 ? nextColor : "#cbd5e1");
    setAreaFillModeInput(nextFillMode === "solid" ? "solid" : "conditional");
    setAreaFillOpacityInput(Number.isFinite(nextFillOpacity) ? Math.max(0, Math.min(100, nextFillOpacity)) : 50);
    setAreaConditionalUpColorInput(
      typeof nextConditionalUpColor === "string" && nextConditionalUpColor.trim()
        ? nextConditionalUpColor
        : "#22c55e"
    );
    setAreaConditionalDownColorInput(
      typeof nextConditionalDownColor === "string" && nextConditionalDownColor.trim()
        ? nextConditionalDownColor
        : "#ef4444"
    );
    setAreaUseSourceStyleInput(nextUseSourceStyle !== false);
    setAreaShowLabelsInput(nextShowLabels !== false);
    setForceSubgraphInput(subscription.config?.force_subgraph === true);
    setActionError(null);
    setSaving(false);
    setDeleting(false);
    // IMPORTANT: dep array intentionally omits `editableFields` and `inferredIndicator`.
    // Those are useMemos derived from `subscription` (a prop). The background 5-second
    // poll in useAgentSubscriptions replaces the subscriptions array with new object
    // references every 5s, causing useMemos to recompute (new reference = new dep value)
    // and triggering this effect — resetting all in-flight form edits.
    // Limiting deps to [isOpen, subscription?.id] means the form only resets when the
    // modal opens or switches to a genuinely different subscription, which is correct.
    // The closure captures editableFields/inferredIndicator at run-time (current render)
    // so the initial population is still accurate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, subscription?.id]);

  if (!isOpen || !subscription) return null;

  const statusEmoji = getStatusEmoji(subscription.status);
  const statusColor = getStatusColor(subscription.status);
  const isIndicator = subscription.agent_type === "indicator";
  const hasAreaOutput = Boolean(subscription.outputs?.some((output) => output?.schema === "area"));
  const resolvedSourceInterval = sourceInterval || subscription.interval;

  const handleVisibleToggle = async (newValue: boolean) => {
    if (!subscription || !onUpdate) {
      return;
    }

    setVisibleInput(newValue);
    setSaving(true);
    setActionError(null);

    try {
      const nextConfig = {
        ...(subscription.config || {}),
        visible: newValue,
      };
      await onUpdate(subscription.id, {
        name: subscription.name,
        config: nextConfig,
      });
      setSavedVisibleInput(newValue);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to save visibility setting.");
      setVisibleInput(savedVisibleInput);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    if (!subscription || !onUpdate) {
      return;
    }

    const nextConfig: Record<string, unknown> = {
      ...(subscription.config || {}),
    };

    editableFields.forEach((field) => {
      delete nextConfig[field.key];
    });

    delete nextConfig.aggregation_interval;

    for (const field of editableFields) {
      const rawValue = (paramInputs[field.key] || "").trim();

      if (field.required && !rawValue) {
        setActionError(`${field.label} is required.`);
        return;
      }

      if (!rawValue) {
        continue;
      }

      const parsedValue = parseFieldValue(rawValue, field.type);
      if ((field.type === "integer" || field.type === "number") && Number.isNaN(parsedValue)) {
        setActionError(`${field.label} must be a valid number.`);
        return;
      }

      if (typeof parsedValue === "number") {
        if (field.min !== undefined && parsedValue < field.min) {
          setActionError(`${field.label} must be greater than or equal to ${field.min}.`);
          return;
        }
        if (field.max !== undefined && parsedValue > field.max) {
          setActionError(`${field.label} must be less than or equal to ${field.max}.`);
          return;
        }
      }

      nextConfig[field.key] = parsedValue;
    }

    const aggregationValidationError = validateAggregationInterval(
      resolvedSourceInterval,
      aggregationIntervalInput
    );
    if (aggregationValidationError) {
      setActionError(aggregationValidationError);
      return;
    }

    if (aggregationIntervalInput.trim()) {
      nextConfig.aggregation_interval = aggregationIntervalInput.trim();
    }

    setSaving(true);
    setActionError(null);
    nextConfig.visible = visibleInput;
    nextConfig.force_subgraph = forceSubgraphInput;
    try {
      nextConfig.line_color = lineColorInput;
      if (hasAreaOutput) {
        nextConfig.area_fill_mode = areaFillModeInput;
        nextConfig.area_fill_opacity = Math.max(0, Math.min(100, areaFillOpacityInput));
        nextConfig.area_conditional_up_color = areaConditionalUpColorInput;
        nextConfig.area_conditional_down_color = areaConditionalDownColorInput;
        nextConfig.area_use_source_style = areaUseSourceStyleInput;
        nextConfig.area_show_labels = areaShowLabelsInput;
      }

      await onUpdate(subscription.id, {
        name: agentNameInput.trim() || subscription.name,
        config: nextConfig,
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
            <div className="agent-header-controls">
              {isIndicator && (
                <label className="header-visible-toggle" htmlFor="agent-visible-toggle-header">
                  <span className="toggle-label">Visible</span>
                  <input
                    id="agent-visible-toggle-header"
                    type="checkbox"
                    checked={visibleInput}
                    onChange={(event) => handleVisibleToggle(event.target.checked)}
                    disabled={saving || deleting}
                  />
                  <span className="toggle-slider" aria-hidden="true" />
                </label>
              )}
              <button className="agent-modal-close" onClick={onClose}>×</button>
            </div>
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
                    <span className="config-label">{humanizeConfigKey(key)}</span>
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
                  <span className="config-label">Aggregation Interval</span>
                  <input
                    className="agent-input"
                    type="text"
                    list="canonical-aggregation-intervals"
                    placeholder={resolvedSourceInterval || "1m"}
                    value={aggregationIntervalInput}
                    onChange={(event) => setAggregationIntervalInput(event.target.value)}
                    disabled={saving || deleting}
                  />
                  <datalist id="canonical-aggregation-intervals">
                    {CANONICAL_INTERVALS.map((interval) => (
                      <option key={interval} value={interval} />
                    ))}
                  </datalist>
                </div>
                {editableFields.map((field) =>
                  field.enumOptions && field.enumOptions.length > 0 ? (
                    <div key={field.key} className="config-item">
                      <span className="config-label">
                        {field.label}
                        {field.required ? " *" : ""}
                      </span>
                      <select
                        className="agent-input"
                        value={paramInputs[field.key] ?? ""}
                        onChange={(event) =>
                          setParamInputs((current) => ({
                            ...current,
                            [field.key]: event.target.value,
                          }))
                        }
                        disabled={saving || deleting}
                      >
                        {!field.required && <option value="">(default)</option>}
                        {field.enumOptions.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : field.type === "boolean" ? (
                    <div key={field.key} className="config-item">
                      <span className="config-label">{field.label}</span>
                      <select
                        className="agent-input"
                        value={paramInputs[field.key] ?? "false"}
                        onChange={(event) =>
                          setParamInputs((current) => ({
                            ...current,
                            [field.key]: event.target.value,
                          }))
                        }
                        disabled={saving || deleting}
                      >
                        <option value="false">false</option>
                        <option value="true">true</option>
                      </select>
                    </div>
                  ) : (
                    <div key={field.key} className="config-item">
                      <span className="config-label">
                        {field.label}
                        {field.required ? " *" : ""}
                      </span>
                      <input
                        className="agent-input"
                        type={field.type === "integer" || field.type === "number" ? "number" : "text"}
                        min={field.min}
                        max={field.max}
                        step={field.type === "integer" ? 1 : field.type === "number" ? "any" : undefined}
                        value={paramInputs[field.key] ?? ""}
                        onChange={(event) =>
                          setParamInputs((current) => ({
                            ...current,
                            [field.key]: event.target.value,
                          }))
                        }
                        disabled={saving || deleting}
                      />
                    </div>
                  )
                )}
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
                {hasAreaOutput && (
                  <>
                    <div className="config-item">
                      <span className="config-label">Area Source Style</span>
                      <label
                        className="agent-subgraph-toggle"
                        style={{ display: "flex", alignItems: "center", gap: "8px", cursor: saving || deleting ? "not-allowed" : "pointer" }}
                      >
                        <input
                          type="checkbox"
                          checked={areaUseSourceStyleInput}
                          onChange={(event) => setAreaUseSourceStyleInput(event.target.checked)}
                          disabled={saving || deleting}
                          style={{ width: "16px", height: "16px", accentColor: "#22c55e", cursor: "inherit" }}
                        />
                        <span style={{ fontSize: "12px", color: areaUseSourceStyleInput ? "#22c55e" : "#94a3b8" }}>
                          {areaUseSourceStyleInput
                            ? "Enabled — use indicator-sent color/opacity metadata"
                            : "Disabled — use UI area colors and opacity overrides"}
                        </span>
                      </label>
                    </div>
                    <div className="config-item">
                      <span className="config-label">Area Labels</span>
                      <label
                        className="agent-subgraph-toggle"
                        style={{ display: "flex", alignItems: "center", gap: "8px", cursor: saving || deleting ? "not-allowed" : "pointer" }}
                      >
                        <input
                          type="checkbox"
                          checked={areaShowLabelsInput}
                          onChange={(event) => setAreaShowLabelsInput(event.target.checked)}
                          disabled={saving || deleting}
                          style={{ width: "16px", height: "16px", accentColor: "#22c55e", cursor: "inherit" }}
                        />
                        <span style={{ fontSize: "12px", color: areaShowLabelsInput ? "#22c55e" : "#94a3b8" }}>
                          {areaShowLabelsInput
                            ? "Enabled — show metadata.label on active zone"
                            : "Disabled — hide zone labels"}
                        </span>
                      </label>
                    </div>
                    <div className="config-item">
                      <span className="config-label">Area Fill Mode</span>
                      <select
                        className="agent-input"
                        value={areaFillModeInput}
                        onChange={(event) =>
                          setAreaFillModeInput(event.target.value === "solid" ? "solid" : "conditional")
                        }
                        disabled={saving || deleting || areaUseSourceStyleInput}
                      >
                        <option value="conditional">Conditional (green/red cloud)</option>
                        <option value="solid">Solid (single fill color)</option>
                      </select>
                    </div>
                    <div className="config-item">
                      <span className="config-label">Area Fill Opacity (%)</span>
                      <input
                        className="agent-input"
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        value={areaFillOpacityInput}
                        onChange={(event) => {
                          const nextValue = Number.parseFloat(event.target.value);
                          if (!Number.isFinite(nextValue)) {
                            setAreaFillOpacityInput(50);
                            return;
                          }
                          setAreaFillOpacityInput(Math.max(0, Math.min(100, nextValue)));
                        }}
                        disabled={saving || deleting || areaUseSourceStyleInput}
                      />
                    </div>
                    <div className="config-item">
                      <span className="config-label">Conditional Up Color</span>
                      <input
                        className="agent-input agent-input-color"
                        type="color"
                        value={areaConditionalUpColorInput}
                        onChange={(event) => setAreaConditionalUpColorInput(event.target.value)}
                        disabled={saving || deleting || areaUseSourceStyleInput}
                      />
                    </div>
                    <div className="config-item">
                      <span className="config-label">Conditional Down Color</span>
                      <input
                        className="agent-input agent-input-color"
                        type="color"
                        value={areaConditionalDownColorInput}
                        onChange={(event) => setAreaConditionalDownColorInput(event.target.value)}
                        disabled={saving || deleting || areaUseSourceStyleInput}
                      />
                    </div>
                  </>
                )}
                <div className="config-item">
                  <span className="config-label">Sub-graph Pane</span>
                  <label className="agent-subgraph-toggle" style={{ display: "flex", alignItems: "center", gap: "8px", cursor: saving || deleting ? "not-allowed" : "pointer" }}>
                    <input
                      type="checkbox"
                      checked={forceSubgraphInput}
                      onChange={(event) => setForceSubgraphInput(event.target.checked)}
                      disabled={saving || deleting}
                      style={{ width: "16px", height: "16px", accentColor: "#22c55e", cursor: "inherit" }}
                    />
                    <span style={{ fontSize: "12px", color: forceSubgraphInput ? "#22c55e" : "#94a3b8" }}>
                      {forceSubgraphInput ? "Enabled — rendered in separate pane" : "Disabled — overlaid on price chart"}
                    </span>
                  </label>
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
