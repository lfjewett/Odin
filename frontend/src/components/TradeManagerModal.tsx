import React, { useEffect, useState } from 'react';
import { getSessionVariables, Variable } from '../workspace/workspaceApi';
import './TradeManagerModal.css';

interface TradeManagerModalProps {
  sessionId: string;
  onClose: () => void;
}

export const TradeManagerModal: React.FC<TradeManagerModalProps> = ({ sessionId, onClose }) => {
  const [variables, setVariables] = useState<Variable[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchVariables = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await getSessionVariables(sessionId);
        setVariables(response.variables);
      } catch (err) {
        console.error('Failed to fetch session variables:', err);
        setError(err instanceof Error ? err.message : 'Failed to load variables');
      } finally {
        setLoading(false);
      }
    };

    fetchVariables();
  }, [sessionId]);

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

  return (
    <div className="trade-manager-overlay" onClick={onClose}>
      <div className="trade-manager-content" onClick={(e) => e.stopPropagation()}>
        <div className="trade-manager-header">
          <h2>Trade Manager - Available Variables</h2>
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
            <div className="trade-manager-error">
              <p>Error: {error}</p>
              <button onClick={() => window.location.reload()}>Retry</button>
            </div>
          )}

          {!loading && !error && (
            <>
              <section className="variable-section">
                <h3 className="section-title">OHLCV Data</h3>
                <div className="variable-list">
                  {ohlcvVariables.length === 0 ? (
                    <p className="empty-state">No OHLCV data available</p>
                  ) : (
                    ohlcvVariables.map((variable, idx) => (
                      <div key={idx} className="variable-item ohlcv-variable">
                        <span className="variable-name">{variable.name}</span>
                        <span className="variable-schema">{variable.schema}</span>
                      </div>
                    ))
                  )}
                </div>
              </section>

              <section className="variable-section">
                <h3 className="section-title">Indicators</h3>
                <div className="variable-list">
                  {indicatorVariables.length === 0 ? (
                    <p className="empty-state">
                      No indicators added yet. Add indicators from the Overlay Agents panel to see them here.
                    </p>
                  ) : (
                    indicatorVariables.map((variable, idx) => (
                      <div key={idx} className="variable-item indicator-variable">
                        <span className="variable-name">{variable.name}</span>
                        <span className="variable-schema">{variable.schema}</span>
                      </div>
                    ))
                  )}
                </div>
              </section>

              <div className="trade-manager-footer">
                <p className="help-text">
                  Add indicators to your chart to see more variables available for trading rules.
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
