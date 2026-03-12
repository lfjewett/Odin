import React, { useEffect, useState } from 'react';
import {
  applyTradeStrategy,
  ApplyTradeStrategyResponse,
  deleteTradeStrategy,
  getTradeStrategy,
  getSessionVariables,
  listTradeStrategies,
  saveTradeStrategy,
  validateTradeStrategy,
  Variable,
} from '../workspace/workspaceApi';
import './TradeManagerModal.css';

interface TradeManagerModalProps {
  sessionId: string;
  appliedStrategyName?: string | null;
  onApply: (result: ApplyTradeStrategyResponse) => void;
  onClose: () => void;
}

const DEFAULT_ENTRY_RULE = 'CLOSE < OPEN AND !IN_BULL_TRADE';
const DEFAULT_EXIT_RULE = 'CLOSE > OPEN AND IN_BULL_TRADE';

type ParsedStrategyRules = {
  long_entry_rules: string[];
  long_exit_rules: string[];
  short_entry_rules: string[];
  short_exit_rules: string[];
};

const buildRulesText = (rules: ParsedStrategyRules): string => {
  const lines: string[] = [];

  rules.long_entry_rules.forEach((rule, idx) => {
    const clean = rule.trim();
    if (clean) {
      lines.push(`long_entry_${idx + 1}: ${clean}`);
    }
  });

  rules.long_exit_rules.forEach((rule, idx) => {
    const clean = rule.trim();
    if (clean) {
      lines.push(`long_exit_${idx + 1}: ${clean}`);
    }
  });

  const hasShortSide = rules.short_entry_rules.some((rule) => rule.trim()) || rules.short_exit_rules.some((rule) => rule.trim());
  if (hasShortSide) {
    lines.push('');
    rules.short_entry_rules.forEach((rule, idx) => {
      const clean = rule.trim();
      if (clean) {
        lines.push(`short_entry_${idx + 1}: ${clean}`);
      }
    });
    rules.short_exit_rules.forEach((rule, idx) => {
      const clean = rule.trim();
      if (clean) {
        lines.push(`short_exit_${idx + 1}: ${clean}`);
      }
    });
  }

  return lines.join('\n');
};

const parseRulesText = (rulesText: string): { rules: ParsedStrategyRules; errors: string[] } => {
  const errors: string[] = [];
  const longEntries: Array<{ index: number; rule: string }> = [];
  const longExits: Array<{ index: number; rule: string }> = [];
  const shortEntries: Array<{ index: number; rule: string }> = [];
  const shortExits: Array<{ index: number; rule: string }> = [];
  let legacyLongEntry = ''; // Support old unnumbered format
  let legacyShortEntry = '';

  const lines = rulesText.split(/\r?\n/);
  lines.forEach((rawLine, lineIdx) => {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      return;
    }

    const colonIdx = line.indexOf(':');
    if (colonIdx <= 0) {
      errors.push(`Line ${lineIdx + 1}: expected 'key: expression' format`);
      return;
    }

    const key = line.slice(0, colonIdx).trim().toLowerCase();
    const value = line.slice(colonIdx + 1).trim();
    if (!value) {
      errors.push(`Line ${lineIdx + 1}: expression cannot be empty`);
      return;
    }

    // Support both old unnumbered format (long_entry) and new numbered format (long_entry_1)
    if (key === 'long_entry') {
      if (legacyLongEntry) {
        errors.push(`Line ${lineIdx + 1}: duplicate long_entry`);
      }
      legacyLongEntry = value;
      return;
    }

    if (key === 'short_entry') {
      if (legacyShortEntry) {
        errors.push(`Line ${lineIdx + 1}: duplicate short_entry`);
      }
      legacyShortEntry = value;
      return;
    }

    const longEntryMatch = key.match(/^long_entry_(\d+)$/);
    if (longEntryMatch) {
      longEntries.push({ index: Number(longEntryMatch[1]), rule: value });
      return;
    }

    const longExitMatch = key.match(/^long_exit_(\d+)$/);
    if (longExitMatch) {
      longExits.push({ index: Number(longExitMatch[1]), rule: value });
      return;
    }

    const shortEntryMatch = key.match(/^short_entry_(\d+)$/);
    if (shortEntryMatch) {
      shortEntries.push({ index: Number(shortEntryMatch[1]), rule: value });
      return;
    }

    const shortExitMatch = key.match(/^short_exit_(\d+)$/);
    if (shortExitMatch) {
      shortExits.push({ index: Number(shortExitMatch[1]), rule: value });
      return;
    }

    errors.push(`Line ${lineIdx + 1}: unsupported key '${key}'`);
  });

  // Legacy support: if old unnumbered format was used, convert to numbered
  if (legacyLongEntry && longEntries.length === 0) {
    longEntries.push({ index: 1, rule: legacyLongEntry });
  }
  if (legacyShortEntry && shortEntries.length === 0) {
    shortEntries.push({ index: 1, rule: legacyShortEntry });
  }

  const dedupeCheck = (entries: Array<{ index: number; rule: string }>, prefix: string) => {
    const seen = new Set<number>();
    entries.forEach(({ index }) => {
      if (seen.has(index)) {
        errors.push(`Duplicate ${prefix}_${index}`);
      }
      seen.add(index);
    });
  };

  dedupeCheck(longEntries, 'long_entry');
  dedupeCheck(longExits, 'long_exit');
  dedupeCheck(shortEntries, 'short_entry');
  dedupeCheck(shortExits, 'short_exit');

  const sortByIndex = (a: { index: number }, b: { index: number }) => a.index - b.index;

  return {
    rules: {
      long_entry_rules: longEntries.sort(sortByIndex).map((entry) => entry.rule),
      long_exit_rules: longExits.sort(sortByIndex).map((entry) => entry.rule),
      short_entry_rules: shortEntries.sort(sortByIndex).map((entry) => entry.rule),
      short_exit_rules: shortExits.sort(sortByIndex).map((entry) => entry.rule),
    },
    errors,
  };
};

export const TradeManagerModal: React.FC<TradeManagerModalProps> = ({ sessionId, appliedStrategyName, onApply, onClose }) => {
  const [variables, setVariables] = useState<Variable[]>([]);
  const [strategyNames, setStrategyNames] = useState<string[]>([]);
  const [selectedStrategyName, setSelectedStrategyName] = useState<string | null>(null);
  const [strategyNameInput, setStrategyNameInput] = useState('MVP Strategy');
  const [strategyRulesText, setStrategyRulesText] = useState(() =>
    buildRulesText({
      long_entry_rules: [DEFAULT_ENTRY_RULE],
      long_exit_rules: [DEFAULT_EXIT_RULE],
      short_entry_rules: [],
      short_exit_rules: [],
    })
  );
  const [description, setDescription] = useState('');
  const [validationResult, setValidationResult] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showLoadDialog, setShowLoadDialog] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newStrategyName, setNewStrategyName] = useState('');

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        setStatusMessage(null);
        const [variablesResult, strategiesResult] = await Promise.allSettled([
          getSessionVariables(sessionId),
          listTradeStrategies(sessionId),
        ]);

        if (variablesResult.status === 'fulfilled') {
          setVariables(variablesResult.value.variables);
        } else {
          console.error('Failed to fetch session variables:', variablesResult.reason);
          setVariables([]);
        }

        if (strategiesResult.status === 'fulfilled') {
          setStrategyNames(strategiesResult.value.strategies.map((strategy) => strategy.name));
        } else {
          console.error('Failed to fetch trade strategies:', strategiesResult.reason);
          setStrategyNames([]);
        }

        if (variablesResult.status === 'rejected' || strategiesResult.status === 'rejected') {
          setError('Some trade manager data could not be loaded. You can still edit/apply strategies.');
        }

        // Auto-load the currently applied strategy so the user can iterate on it
        if (appliedStrategyName) {
          try {
            const strategy = await getTradeStrategy(sessionId, appliedStrategyName);
            setSelectedStrategyName(strategy.name);
            setStrategyNameInput(strategy.name);
            setDescription(strategy.description || '');
            setStrategyRulesText(
              buildRulesText({
                long_entry_rules: Array.isArray(strategy.long_entry_rules) ? strategy.long_entry_rules : [],
                long_exit_rules: Array.isArray(strategy.long_exit_rules) ? strategy.long_exit_rules : [],
                short_entry_rules: Array.isArray(strategy.short_entry_rules) ? strategy.short_entry_rules : [],
                short_exit_rules: Array.isArray(strategy.short_exit_rules) ? strategy.short_exit_rules : [],
              }),
            );
          } catch {
            // Strategy may not be persisted yet; keep blank defaults
          }
        }
      } catch (err) {
        console.error('Unexpected failure while loading Trade Manager data:', err);
        setError(err instanceof Error ? err.message : 'Failed to load Trade Manager data');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [sessionId, appliedStrategyName]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose]);

  const ohlcvVariables = variables.filter(v => v.type === 'ohlcv');
  const indicatorVariables = variables.filter(v => v.type === 'indicator');

  const refreshStrategies = async () => {
    const response = await listTradeStrategies(sessionId);
    setStrategyNames(response.strategies.map((strategy) => strategy.name));
  };

  const openCreateDialog = () => {
    setNewStrategyName('');
    setShowCreateDialog(true);
  };

  const handleCreateConfirm = () => {
    const name = newStrategyName.trim();
    if (!name) {
      setError('Please provide a strategy name.');
      return;
    }
    handleNew();
    setStrategyNameInput(name);
    setSelectedStrategyName(null);
    setShowCreateDialog(false);
    setStatusMessage(`Created new strategy draft: ${name}`);
  };

  const handleNew = () => {
    setSelectedStrategyName(null);
    setStrategyNameInput('MVP Strategy');
    setDescription('');
    setStrategyRulesText(
      buildRulesText({
        long_entry_rules: [DEFAULT_ENTRY_RULE],
        long_exit_rules: [DEFAULT_EXIT_RULE],
        short_entry_rules: [],
        short_exit_rules: [],
      })
    );
    setValidationResult(null);
    setStatusMessage('Started a new strategy draft.');
  };

  const handleLoad = async (strategyName: string) => {
    try {
      setBusyAction('loading');
      setStatusMessage(null);
      const strategy = await getTradeStrategy(sessionId, strategyName);
      setSelectedStrategyName(strategy.name);
      setStrategyNameInput(strategy.name);
      setDescription(strategy.description || '');
      setStrategyRulesText(
        buildRulesText({
          long_entry_rules: Array.isArray(strategy.long_entry_rules) ? strategy.long_entry_rules : [],
          long_exit_rules: Array.isArray(strategy.long_exit_rules) ? strategy.long_exit_rules : [],
          short_entry_rules: Array.isArray(strategy.short_entry_rules) ? strategy.short_entry_rules : [],
          short_exit_rules: Array.isArray(strategy.short_exit_rules) ? strategy.short_exit_rules : [],
        })
      );
      setValidationResult(null);
      setStatusMessage(`Loaded strategy: ${strategy.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load strategy');
    } finally {
      setBusyAction(null);
    }
  };

  const handleValidate = async () => {
    try {
      setBusyAction('validating');
      setError(null);
      setStatusMessage(null);
      const parsed = parseRulesText(strategyRulesText);
      if (parsed.errors.length > 0) {
        setValidationResult(`Rule format errors:\n${parsed.errors.join('\n')}`);
        return;
      }
      const result = await validateTradeStrategy(sessionId, {
        long_entry_rules: parsed.rules.long_entry_rules,
        long_exit_rules: parsed.rules.long_exit_rules,
        short_entry_rules: parsed.rules.short_entry_rules,
        short_exit_rules: parsed.rules.short_exit_rules,
      });
      if (result.valid) {
        setValidationResult('Valid strategy syntax.');
      } else {
        setValidationResult(`Validation errors:\n${result.errors.join('\n')}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Validation failed');
    } finally {
      setBusyAction(null);
    }
  };

  const handleSave = async () => {
    const name = strategyNameInput.trim();
    if (!name) {
      setError('Strategy name is required to save.');
      return;
    }

    try {
      setBusyAction('saving');
      setError(null);
      setStatusMessage(null);
      const parsed = parseRulesText(strategyRulesText);
      if (parsed.errors.length > 0) {
        setError(`Rule format errors:\n${parsed.errors.join('\n')}`);
        return;
      }
      const saved = await saveTradeStrategy(sessionId, name, {
        description,
        long_entry_rules: parsed.rules.long_entry_rules,
        long_exit_rules: parsed.rules.long_exit_rules,
        short_entry_rules: parsed.rules.short_entry_rules,
        short_exit_rules: parsed.rules.short_exit_rules,
      });
      setSelectedStrategyName(saved.name);
      setStrategyNameInput(saved.name);
      await refreshStrategies();
      setStatusMessage(`Saved strategy: ${saved.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save strategy');
    } finally {
      setBusyAction(null);
    }
  };

  const handleDelete = async () => {
    const name = selectedStrategyName || strategyNameInput.trim();
    if (!name) {
      return;
    }
    const confirmed = window.confirm(`Delete strategy "${name}"?`);
    if (!confirmed) {
      return;
    }

    try {
      setBusyAction('deleting');
      setError(null);
      await deleteTradeStrategy(sessionId, name);
      await refreshStrategies();
      handleNew();
      setStatusMessage(`Deleted strategy: ${name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete strategy');
    } finally {
      setBusyAction(null);
    }
  };

  const handleApply = async () => {
    try {
      setBusyAction('applying');
      setError(null);
      setStatusMessage(null);
      const name = strategyNameInput.trim();
      const parsed = parseRulesText(strategyRulesText);
      if (parsed.errors.length > 0) {
        setError(`Rule format errors:\n${parsed.errors.join('\n')}`);
        return;
      }
      console.log('[TradeManagerModal] Apply requested', {
        sessionId,
        strategyName: name,
        hasLongEntryRule: Boolean(parsed.rules.long_entry_rules.some((rule) => rule.trim())),
        longExitRuleCount: parsed.rules.long_exit_rules.length,
        hasShortEntryRule: Boolean(parsed.rules.short_entry_rules.some((rule) => rule.trim())),
        shortExitRuleCount: parsed.rules.short_exit_rules.length,
      });

      if (name) {
        try {
          const saved = await saveTradeStrategy(sessionId, name, {
            description,
            long_entry_rules: parsed.rules.long_entry_rules,
            long_exit_rules: parsed.rules.long_exit_rules,
            short_entry_rules: parsed.rules.short_entry_rules,
            short_exit_rules: parsed.rules.short_exit_rules,
          });
          setSelectedStrategyName(saved.name);
          setStrategyNameInput(saved.name);
          await refreshStrategies();
        } catch (saveErr) {
          console.warn('Apply continued even though auto-save failed:', saveErr);
        }
      }

      const response = await applyTradeStrategy(sessionId, {
        strategy_name: name || undefined,
        long_entry_rules: parsed.rules.long_entry_rules,
        long_exit_rules: parsed.rules.long_exit_rules,
        short_entry_rules: parsed.rules.short_entry_rules,
        short_exit_rules: parsed.rules.short_exit_rules,
      });
      console.log('[TradeManagerModal] Apply response', {
        strategyName: response.strategy_name,
        markerCount: response.marker_count,
        hasPerformance: Boolean(response.performance),
      });
      onApply(response);
      setStatusMessage(`Applied strategy. Generated ${response.marker_count} marker(s).`);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to apply strategy');
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div className="trade-manager-overlay" onClick={onClose}>
      <div className="trade-manager-content" onClick={(e) => e.stopPropagation()}>
        <div className="trade-manager-header">
          <h2>Trade Manager</h2>
          <button className="trade-manager-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="trade-manager-body">
          {loading && (
            <div className="trade-manager-loading">
              <div className="loading-spinner"></div>
              <p>Loading variables...</p>
            </div>
          )}

          {error && (
            <div className="inline-error-banner">
              <span>⚠️ {error}</span>
              <button onClick={() => setError(null)}>×</button>
            </div>
          )}

          {!loading && (
            <>
              <section className="variable-section">
                <h3 className="section-title">Strategy Controls</h3>
                <div className="strategy-toolbar strategy-toolbar-top">
                  <button className="trade-button" onClick={() => setShowLoadDialog(true)} disabled={busyAction !== null}>Load</button>
                  <button className="trade-button" onClick={openCreateDialog}>Create</button>
                  <button className="trade-button danger" onClick={handleDelete} disabled={!selectedStrategyName && !strategyNameInput.trim()}>
                    Delete
                  </button>
                  <button className="trade-button" onClick={handleValidate} disabled={busyAction !== null}>Validate</button>
                  <button className="trade-button" onClick={handleSave} disabled={busyAction !== null}>Save</button>
                  <button className="trade-button primary" onClick={handleApply} disabled={busyAction !== null}>Apply</button>
                </div>
              </section>

              <section className="variable-section">
                <h3 className="section-title">Strategy Editor</h3>
                <div className="editor-grid">
                  <label className="editor-label">
                    <span>Strategy Name</span>
                    <input
                      className="editor-input"
                      value={strategyNameInput}
                      onChange={(event) => setStrategyNameInput(event.target.value)}
                      placeholder="My strategy"
                    />
                  </label>
                  <label className="editor-label">
                    <span>Description</span>
                    <input
                      className="editor-input"
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      placeholder="Optional notes"
                    />
                  </label>
                  <label className="editor-label full-width">
                    <span>Strategy Rules</span>
                    <textarea
                      className="editor-textarea strategy-rules-textarea"
                      value={strategyRulesText}
                      onChange={(event) => setStrategyRulesText(event.target.value)}
                      rows={14}
                    />
                    <p className="rules-hint">
                      Required keys: <strong>long_entry</strong>, one or more <strong>long_exit_1..N</strong>.
                      Optional: <strong>short_entry</strong> and <strong>short_exit_1..N</strong>.
                      Comments supported with <strong>#</strong>.
                    </p>
                  </label>
                </div>
              </section>

              <section className="variable-section variable-section-compact">
                <h3 className="section-title">Available Variables</h3>
                <div className="variables-board">
                  <div>
                    <h4 className="compact-title">OHLCV</h4>
                    <div className="variable-chip-list">
                  {ohlcvVariables.length === 0 ? (
                    <p className="empty-state">No OHLCV data available</p>
                  ) : (
                    ohlcvVariables.map((variable, idx) => (
                      <div key={idx} className="variable-chip variable-chip-ohlcv" title={`${variable.name} (${variable.schema})`}>
                        {variable.name}
                      </div>
                    ))
                  )}
                    </div>
                  </div>
                  <div>
                    <h4 className="compact-title">Indicators</h4>
                    <div className="variable-chip-list">
                  {indicatorVariables.length === 0 ? (
                    <p className="empty-state">
                      No indicators added yet. Add indicators from the Overlay Agents panel to see them here.
                    </p>
                  ) : (
                    indicatorVariables.map((variable, idx) => (
                      <div key={idx} className="variable-chip variable-chip-indicator" title={`${variable.name} (${variable.schema})`}>
                        {variable.name}
                      </div>
                    ))
                  )}
                    </div>
                  </div>
                </div>
              </section>

              {validationResult && <pre className="validation-output">{validationResult}</pre>}
              {statusMessage && <p className="help-text">{statusMessage}</p>}

              <div className="trade-manager-footer">
                <p className="help-text">
                  Configure long and short sides independently. Multiple exit rules are OR-ed per side (TP + SL + time exits).
                  <br />
                  <a href="https://github.com/ljewett/Odin/blob/main/docs/trade-manager-phase0-dsl.md" target="_blank" rel="noopener noreferrer" className="dsl-docs-link">
                    View full DSL documentation and examples →
                  </a>
                </p>
              </div>

              {showLoadDialog && (
                <div className="dialog-backdrop" onClick={() => setShowLoadDialog(false)}>
                  <div className="dialog-card" onClick={(event) => event.stopPropagation()}>
                    <h4>Load Strategy</h4>
                    {strategyNames.length === 0 ? (
                      <p className="help-text">No saved strategies yet. Click Create to make your first one.</p>
                    ) : (
                      <div className="dialog-list">
                        {strategyNames.map((name) => (
                          <button
                            key={name}
                            className="dialog-list-item"
                            onClick={() => {
                              void handleLoad(name);
                              setShowLoadDialog(false);
                            }}
                          >
                            {name}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="dialog-actions">
                      <button className="trade-button" onClick={() => setShowLoadDialog(false)}>Close</button>
                    </div>
                  </div>
                </div>
              )}

              {showCreateDialog && (
                <div className="dialog-backdrop" onClick={() => setShowCreateDialog(false)}>
                  <div className="dialog-card" onClick={(event) => event.stopPropagation()}>
                    <h4>Create Strategy</h4>
                    <label className="editor-label full-width">
                      <span>Strategy Name</span>
                      <input
                        className="editor-input"
                        value={newStrategyName}
                        onChange={(event) => setNewStrategyName(event.target.value)}
                        placeholder="e.g. Pullback v1"
                        autoFocus
                      />
                    </label>
                    <div className="dialog-actions">
                      <button className="trade-button" onClick={() => setShowCreateDialog(false)}>Cancel</button>
                      <button className="trade-button primary" onClick={handleCreateConfirm}>Create</button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
