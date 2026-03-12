import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  evaluateResearchExpression,
  getSessionVariables,
  type EvaluateResearchExpressionResponse,
  type ResearchOutputSchema,
  type Variable,
} from "../workspace/workspaceApi";
import "./ResearchModal.css";

const NAMED_COLORS: Record<string, string> = {
  RED: "#ef4444",
  GREEN: "#22c55e",
  BLUE: "#3b82f6",
  YELLOW: "#eab308",
  ORANGE: "#f97316",
  PURPLE: "#a855f7",
  CYAN: "#06b6d4",
  WHITE: "#ffffff",
  GRAY: "#94a3b8",
};

const BUILT_IN_VARIABLES = [
  "OPEN",
  "HIGH",
  "LOW",
  "CLOSE",
  "VOLUME",
  "TIME",
  "HOUR",
  "MINUTE",
  "BODY",
  "RANGE",
  "UPPER_WICK",
  "LOWER_WICK",
  "DAILY_PNL",
];

interface ParsedResearchLine {
  outputId: string;
  schema: ResearchOutputSchema;
  expression: string;
  color?: string;
  trendColoring?: boolean;
  subgraphGroup?: string;
}

export interface ResearchOverlayEvaluation {
  outputId: string;
  result: EvaluateResearchExpressionResponse;
}

interface ResearchModalProps {
  sessionId: string;
  onClose: () => void;
  onEvaluated: (results: ResearchOverlayEvaluation[]) => void;
  onClear: () => void;
  initialExpression: string;
  onExpressionChange: (expression: string) => void;
  initialOutputSchema: ResearchOutputSchema;
}

function resolveDirectiveColor(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  const namedMatch = /^COLOR\.([A-Z_]+)$/i.exec(trimmed);
  if (namedMatch) {
    return NAMED_COLORS[namedMatch[1].toUpperCase()] || null;
  }

  const hexMatch = /^COLOR\.(#[0-9A-F]{3,8})$/i.exec(trimmed);
  if (hexMatch) {
    return hexMatch[1];
  }

  return null;
}

function parseResearchProgram(program: string, defaultSchema: ResearchOutputSchema): ParsedResearchLine[] {
  const rawLines = program
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("#"));

  const parsed: ParsedResearchLine[] = [];

  rawLines.forEach((line, index) => {
    const directiveParts = line
      .split("@")
      .map((part) => part.trim())
      .filter((part) => part.length > 0);

    if (directiveParts.length === 0) {
      return;
    }

    const body = directiveParts[0];
    const assignmentMatch = /^(line|area|histogram)\s*=\s*(.+)$/i.exec(body);
    const schema = (assignmentMatch ? assignmentMatch[1].toLowerCase() : defaultSchema) as ResearchOutputSchema;
    let expression = (assignmentMatch ? assignmentMatch[2] : body).trim();

    const unaryNumericMatch = /^-\s*(\d+(?:\.\d+)?|\.\d+)$/i.exec(expression);
    if (unaryNumericMatch) {
      expression = `0 - ${unaryNumericMatch[1]}`;
    }

    if (!expression) {
      throw new Error(`Line ${index + 1}: missing expression`);
    }

    let color: string | undefined;
    let trendColoring = false;
    let subgraphGroup = "1";

    for (const directive of directiveParts.slice(1)) {
      if (/^TREND$/i.test(directive)) {
        trendColoring = true;
        continue;
      }

      const subgraphMatch = /^(SUBGRAPH|PANE)\.([A-Z0-9_-]+)$/i.exec(directive);
      if (subgraphMatch) {
        subgraphGroup = subgraphMatch[2];
        continue;
      }

      const resolvedColor = resolveDirectiveColor(directive);
      if (resolvedColor) {
        color = resolvedColor;
        continue;
      }

      throw new Error(`Line ${index + 1}: unsupported directive '${directive}'`);
    }

    parsed.push({
      outputId: `expr_${index + 1}`,
      schema,
      expression,
      color,
      trendColoring,
      subgraphGroup,
    });
  });

  return parsed;
}

function decorateRecords(
  records: EvaluateResearchExpressionResponse["records"],
  color?: string,
  trendColoring?: boolean,
  subgraphGroup?: string,
): EvaluateResearchExpressionResponse["records"] {
  if (!color && !trendColoring && !subgraphGroup) {
    return records;
  }

  return records.map((record) => ({
    ...record,
    metadata: {
      ...(record.metadata || {}),
      ...(color ? { color } : {}),
      ...(trendColoring ? { color_mode: "trend" } : {}),
      ...(subgraphGroup ? { subgraph_group: subgraphGroup } : {}),
    },
  }));
}

export function ResearchModal({
  sessionId,
  onClose,
  onEvaluated,
  onClear,
  initialExpression,
  onExpressionChange,
  initialOutputSchema,
}: ResearchModalProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [variables, setVariables] = useState<Variable[]>([]);
  const [expression, setExpression] = useState(initialExpression);
  const [loadingVariables, setLoadingVariables] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("Ready.");

  const updateExpression = useCallback(
    (nextExpression: string) => {
      setExpression(nextExpression);
      onExpressionChange(nextExpression);
    },
    [onExpressionChange],
  );

  useEffect(() => {
    let cancelled = false;
    const loadVariables = async () => {
      try {
        setLoadingVariables(true);
        const result = await getSessionVariables(sessionId);
        if (!cancelled) {
          setVariables(result.variables || []);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load variables");
          setVariables([]);
        }
      } finally {
        if (!cancelled) {
          setLoadingVariables(false);
        }
      }
    };

    void loadVariables();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const runEvaluation = useCallback(async () => {
    const trimmedExpression = expression.trim();
    if (!trimmedExpression) {
      setStatusMessage("Enter an expression to visualize.");
      setError(null);
      return;
    }

    try {
      setRunning(true);
      setError(null);
      setStatusMessage("Evaluating…");
      const lines = parseResearchProgram(trimmedExpression, initialOutputSchema);
      if (lines.length === 0) {
        setStatusMessage("Enter at least one expression line.");
        return;
      }

      const evaluations: ResearchOverlayEvaluation[] = [];
      let totalPoints = 0;
      for (const line of lines) {
        const result = await evaluateResearchExpression(sessionId, {
          expression: line.expression,
          output_schema: line.schema,
        });
        const decorated = decorateRecords(result.records, line.color, line.trendColoring, line.subgraphGroup);
        totalPoints += result.count;
        evaluations.push({
          outputId: line.outputId,
          result: {
            ...result,
            records: decorated,
          },
        });
      }

      onEvaluated(evaluations);
      setStatusMessage(`Rendered ${totalPoints} points across ${evaluations.length} overlay${evaluations.length === 1 ? "" : "s"}.`);
      onClose();
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Failed to evaluate expression");
      setStatusMessage("Evaluation failed.");
    } finally {
      setRunning(false);
    }
  }, [expression, initialOutputSchema, onEvaluated, sessionId]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "enter") {
        event.preventDefault();
        void runEvaluation();
      }
    };

    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [onClose, runEvaluation]);

  const ohlcvVariables = useMemo(() => variables.filter((item) => item.type === "ohlcv"), [variables]);
  const indicatorVariables = useMemo(() => variables.filter((item) => item.type === "indicator"), [variables]);
  const availableColorNames = useMemo(() => Object.keys(NAMED_COLORS), []);

  const insertVariableToken = useCallback((token: string) => {
    const textarea = textareaRef.current;
    if (!textarea) {
      updateExpression(`${expression}${expression.endsWith(" ") || expression.length === 0 ? "" : " "}${token}`);
      return;
    }

    const start = textarea.selectionStart ?? expression.length;
    const end = textarea.selectionEnd ?? expression.length;
    const value = expression;
    const before = value.slice(0, start);
    const after = value.slice(end);

    const needsLeadingSpace = before.length > 0 && !/\s$/.test(before);
    const needsTrailingSpace = after.length > 0 && !/^\s/.test(after);
    const insertion = `${needsLeadingSpace ? " " : ""}${token}${needsTrailingSpace ? " " : ""}`;
    const nextValue = `${before}${insertion}${after}`;
    const nextCaret = before.length + insertion.length;

    updateExpression(nextValue);
    requestAnimationFrame(() => {
      const active = textareaRef.current;
      if (!active) return;
      active.focus();
      active.setSelectionRange(nextCaret, nextCaret);
    });
  }, [expression, updateExpression]);

  return (
    <div className="research-overlay" role="dialog" aria-modal="true" aria-label="Research strategy">
      <div className="research-content">
        <header className="research-header">
          <h2>Research Strategy</h2>
          <button type="button" className="research-close" onClick={onClose} aria-label="Close research">
            ×
          </button>
        </header>

        <div className="research-body">
          <section className="research-editor">
            <label htmlFor="research-expression" className="research-label">Expression (DSL)</label>
            <textarea
              id="research-expression"
              ref={textareaRef}
              className="research-textarea"
              value={expression}
              onChange={(event) => updateExpression(event.target.value)}
              spellCheck={false}
            />

            <div className="research-state-row">
              {running && <span className="research-running">Evaluating…</span>}
              {!running && statusMessage && <span className="research-status">{statusMessage}</span>}
              {error && <span className="research-error">{error}</span>}
            </div>

            <div className="research-actions">
              <button
                type="button"
                className="research-button"
                onClick={() => {
                  void runEvaluation();
                }}
                disabled={running}
              >
                Apply
              </button>
              <button
                type="button"
                className="research-button research-button-secondary"
                onClick={() => {
                  onClear();
                  onClose();
                }}
                disabled={running}
              >
                Clear Overlay
              </button>
            </div>

            <p className="research-hint">
              Use one expression per line, optionally `line|area|histogram = expr`. Add directives like <strong>@ COLOR.RED</strong>, <strong>@ TREND</strong>, or <strong>@ SUBGRAPH.2</strong>. Press <strong>⌘/Ctrl + Enter</strong> to apply.
            </p>
          </section>

          <aside className="research-variables">
            <h3>Available Variables</h3>
            {loadingVariables ? (
              <div className="research-loading">Loading variables…</div>
            ) : (
              <>
                <div className="research-variable-block">
                  <h4>Schema Helpers</h4>
                  <ul className="research-inline-token-list research-inline-token-list--three">
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("line =")}
                      >
                        line =
                      </button>
                    </li>
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("histogram =")}
                      >
                        histogram =
                      </button>
                    </li>
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("area =")}
                      >
                        area =
                      </button>
                    </li>
                  </ul>
                </div>

                <div className="research-variable-block">
                  <h4>Color Helpers</h4>
                  <ul className="research-inline-token-list">
                    {availableColorNames.map((colorName) => (
                      <li key={colorName}>
                        <button
                          type="button"
                          className="research-variable-token"
                          onClick={() => insertVariableToken(`@ COLOR.${colorName}`)}
                        >
                          COLOR.{colorName}
                        </button>
                      </li>
                    ))}
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("@ TREND")}
                      >
                        TREND
                      </button>
                    </li>
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("@ SUBGRAPH.1")}
                      >
                        SUBGRAPH.1
                      </button>
                    </li>
                    <li>
                      <button
                        type="button"
                        className="research-variable-token"
                        onClick={() => insertVariableToken("@ SUBGRAPH.2")}
                      >
                        SUBGRAPH.2
                      </button>
                    </li>
                  </ul>
                </div>

                <div className="research-variable-grid">
                  <div className="research-variable-block">
                    <h4>Built-in</h4>
                    <ul className="research-inline-token-list research-inline-token-list--three">
                      {BUILT_IN_VARIABLES.map((token) => (
                        <li key={token}>
                          <button
                            type="button"
                            className="research-variable-token"
                            onClick={() => insertVariableToken(token)}
                          >
                            {token}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="research-variable-block">
                    <h4>OHLCV</h4>
                    <ul className="research-inline-token-list research-inline-token-list--three">
                      {ohlcvVariables.map((variable) => (
                        <li key={variable.name}>
                          <button
                            type="button"
                            className="research-variable-token"
                            onClick={() => insertVariableToken(variable.name)}
                          >
                            {variable.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="research-variable-block research-variable-block--indicators">
                    <h4>Indicators</h4>
                    <ul className="research-inline-token-list research-inline-token-list--three">
                      {indicatorVariables.map((variable) => (
                        <li key={variable.name}>
                          <button
                            type="button"
                            className="research-variable-token"
                            onClick={() => insertVariableToken(variable.name)}
                          >
                            {variable.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

export default ResearchModal;
