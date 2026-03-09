import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AgentSubscription,
  CreateSubscriptionPayload,
  DiscoverAgentResult,
} from "../hooks/useAgentSubscriptions";
import "./AddIndicatorAgentModal.css";

interface AddIndicatorAgentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (agent: AgentSubscription) => void;
  discoverAgent: (agentUrl: string) => Promise<DiscoverAgentResult>;
  createSubscription: (payload: CreateSubscriptionPayload) => Promise<AgentSubscription>;
}

export default function AddIndicatorAgentModal({
  isOpen,
  onClose,
  onCreated,
  discoverAgent,
  createSubscription,
}: AddIndicatorAgentModalProps) {
  const [agentUrl, setAgentUrl] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoverAgentResult | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  const [selectedIndicatorId, setSelectedIndicatorId] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [lineColor, setLineColor] = useState<string>("#22c55e");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setAgentUrl("");
      setDiscovering(false);
      setDiscovered(null);
      setDiscoverError(null);
      setSelectedIndicatorId("");
      setParamValues({});
      setLineColor("#22c55e");
      setSubmitError(null);
      setSubmitting(false);
    }
  }, [isOpen]);

  const indicators = discovered?.metadata.indicators || [];

  const selectedIndicator = useMemo(
    () => indicators.find((item) => item.indicator_id === selectedIndicatorId) || null,
    [indicators, selectedIndicatorId]
  );

  const paramsSchema = selectedIndicator?.params_schema || {};

  const setDefaultsForIndicator = (indicatorId: string, discoverResult: DiscoverAgentResult) => {
    const indicator = (discoverResult.metadata.indicators || []).find(
      (item) => item.indicator_id === indicatorId
    );
    const schema = indicator?.params_schema || {};
    const defaults: Record<string, string> = {};

    Object.entries(schema).forEach(([key, definition]) => {
      const typed = definition as Record<string, any>;
      if (typed.default !== undefined && typed.default !== null) {
        defaults[key] = String(typed.default);
      } else {
        defaults[key] = "";
      }
    });

    setParamValues(defaults);
  };

  const handleDiscover = async () => {
    const normalizedUrl = agentUrl.trim();
    if (!normalizedUrl) {
      setDiscoverError("Agent URL is required.");
      return;
    }

    setDiscovering(true);
    setDiscoverError(null);
    setSubmitError(null);

    try {
      const result = await discoverAgent(normalizedUrl);
      if (result.metadata.agent_type !== "indicator") {
        throw new Error(
          `Discovered agent type '${result.metadata.agent_type}'. This modal only adds indicator agents.`
        );
      }

      const discoveredIndicators = result.metadata.indicators || [];
      if (!discoveredIndicators.length) {
        throw new Error("No indicators were found in agent metadata.");
      }

      const firstIndicatorId = discoveredIndicators[0].indicator_id;
      setDiscovered(result);
      setSelectedIndicatorId(firstIndicatorId);
      setDefaultsForIndicator(firstIndicatorId, result);
    } catch (error) {
      setDiscovered(null);
      setSelectedIndicatorId("");
      setParamValues({});
      setDiscoverError(error instanceof Error ? error.message : "Failed to discover agent.");
    } finally {
      setDiscovering(false);
    }
  };

  const handleIndicatorChange = (indicatorId: string) => {
    setSelectedIndicatorId(indicatorId);
    if (discovered) {
      setDefaultsForIndicator(indicatorId, discovered);
    }
  };

  const parseParamValue = (rawValue: string, paramType: string) => {
    if (paramType === "integer") {
      return Number.parseInt(rawValue, 10);
    }
    if (paramType === "number") {
      return Number.parseFloat(rawValue);
    }
    if (paramType === "boolean") {
      return rawValue === "true";
    }
    return rawValue;
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();

    if (!discovered) {
      setSubmitError("Discover an indicator agent first.");
      return;
    }

    if (!selectedIndicator) {
      setSubmitError("Select an indicator.");
      return;
    }

    const params: Record<string, any> = {};

    for (const [key, definition] of Object.entries(paramsSchema)) {
      const typed = definition as Record<string, any>;
      const rawValue = (paramValues[key] || "").trim();
      if (typed.required && !rawValue) {
        setSubmitError(`Missing required parameter: ${key}`);
        return;
      }
      if (!rawValue) {
        continue;
      }
      const parsed = parseParamValue(rawValue, typed.type);
      if ((typed.type === "integer" || typed.type === "number") && Number.isNaN(parsed)) {
        setSubmitError(`Invalid numeric value for parameter: ${key}`);
        return;
      }
      params[key] = parsed;
    }

    // Always include line_color in config
    params.line_color = lineColor;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const created = await createSubscription({
        name: selectedIndicator.name,
        agent_type: "indicator",
        agent_url: discovered.agent_url,
        indicator_id: selectedIndicator.indicator_id,
        config: params,
      });
      onCreated(created);
      onClose();
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Failed to add indicator.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) {
    return null;
  }

  return (
    <div className="add-indicator-modal-overlay" onClick={onClose}>
      <div className="add-indicator-modal" onClick={(event) => event.stopPropagation()}>
        <div className="add-indicator-modal-header">
          <h2>Add Indicator Agent</h2>
          <button type="button" className="add-indicator-close" onClick={onClose}>
            ×
          </button>
        </div>

        <form className="add-indicator-modal-body" onSubmit={handleSubmit}>
          <label className="add-indicator-label" htmlFor="agent-url-input">
            Agent Base URL
          </label>
          <div className="add-indicator-discover-row">
            <input
              id="agent-url-input"
              className="add-indicator-input"
              type="text"
              placeholder="http://localhost:8020"
              value={agentUrl}
              onChange={(event) => setAgentUrl(event.target.value)}
              disabled={discovering || submitting}
            />
            <button
              type="button"
              className="add-indicator-action"
              onClick={handleDiscover}
              disabled={discovering || submitting}
            >
              {discovering ? "Discovering..." : "Discover"}
            </button>
          </div>

          {discoverError && <p className="add-indicator-error">{discoverError}</p>}

          {discovered && (
            <>
              <label className="add-indicator-label" htmlFor="indicator-select">
                Indicator
              </label>
              <select
                id="indicator-select"
                className="add-indicator-select"
                value={selectedIndicatorId}
                onChange={(event) => handleIndicatorChange(event.target.value)}
                disabled={submitting}
              >
                {indicators.map((indicator) => (
                  <option key={indicator.indicator_id} value={indicator.indicator_id}>
                    {indicator.name} ({indicator.indicator_id})
                  </option>
                ))}
              </select>

              {selectedIndicator?.description && (
                <p className="add-indicator-description">{selectedIndicator.description}</p>
              )}

              {Object.keys(paramsSchema).length > 0 && (
                <div className="add-indicator-params-grid">
                  {Object.entries(paramsSchema).map(([key, definition]) => {
                    const typed = definition as Record<string, any>;
                    const inputType = typed.type === "integer" || typed.type === "number" ? "number" : "text";

                    if (typed.type === "boolean") {
                      return (
                        <label key={key} className="add-indicator-param-row">
                          <span>{key}{typed.required ? " *" : ""}</span>
                          <select
                            className="add-indicator-select"
                            value={paramValues[key] ?? "false"}
                            onChange={(event) =>
                              setParamValues((prev) => ({ ...prev, [key]: event.target.value }))
                            }
                            disabled={submitting}
                          >
                            <option value="false">false</option>
                            <option value="true">true</option>
                          </select>
                        </label>
                      );
                    }

                    return (
                      <label key={key} className="add-indicator-param-row">
                        <span>{key}{typed.required ? " *" : ""}</span>
                        <input
                          className="add-indicator-input"
                          type={inputType}
                          value={paramValues[key] ?? ""}
                          onChange={(event) =>
                            setParamValues((prev) => ({ ...prev, [key]: event.target.value }))
                          }
                          min={typed.min}
                          max={typed.max}
                          step={typed.type === "integer" ? "1" : "any"}
                          disabled={submitting}
                        />
                      </label>
                    );
                  })}
                </div>
              )}

              <label className="add-indicator-label" htmlFor="line-color-input">
                Line Color
              </label>
              <div className="add-indicator-color-row">
                <input
                  id="line-color-input"
                  className="add-indicator-color-picker"
                  type="color"
                  value={lineColor}
                  onChange={(event) => setLineColor(event.target.value)}
                  disabled={submitting}
                />
                <span className="add-indicator-color-value">{lineColor}</span>
              </div>
            </>
          )}

          {submitError && <p className="add-indicator-error">{submitError}</p>}

          <div className="add-indicator-footer">
            <button type="button" className="add-indicator-secondary" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="add-indicator-action"
              disabled={!discovered || !selectedIndicator || submitting}
            >
              {submitting ? "Adding..." : "Add Indicator"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
