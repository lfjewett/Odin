import { useState, useMemo, useCallback, useRef, useEffect } from "react";

interface ChartTimeControlProps {
  selectedInterval: string;
  selectedTimeframe: number;
  onIntervalChange: (interval: string) => void;
  onTimeframeChange: (days: number) => void;
}

const INTERVAL_TIMESPAN_CONSTRAINTS: Record<string, number[]> = {
  "1m": [1, 7, 30, 90, 180],
  "5m": [1, 7, 30, 90, 180, 365],
  "15m": [1, 7, 30, 90, 180, 365, 730],
  "30m": [1, 7, 30, 90, 180, 365, 730],
  "1h": [1, 7, 30, 90, 180, 365, 730, 1825],
  "4h": [1, 7, 30, 90, 180, 365, 730, 1825],
  "1d": [1, 7, 30, 90, 180, 365, 730, 1825, 3650],
};

const TIMESPAN_LABELS: Record<number, string> = {
  1: "1D",
  7: "1W",
  30: "1M",
  90: "3M",
  180: "6M",
  365: "1Y",
  730: "2Y",
  1825: "5Y",
  3650: "10Y",
};

export default function ChartTimeControl({
  selectedInterval,
  selectedTimeframe,
  onIntervalChange,
  onTimeframeChange,
}: ChartTimeControlProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const buttonRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  const allowedTimespans = useMemo(
    () => INTERVAL_TIMESPAN_CONSTRAINTS[selectedInterval] || [],
    [selectedInterval]
  );
  const selectedTimeframeLabel =
    TIMESPAN_LABELS[selectedTimeframe] ?? `${selectedTimeframe}D`;

  const handleIntervalChange = useCallback(
    (newInterval: string) => {
      const allowed = INTERVAL_TIMESPAN_CONSTRAINTS[newInterval];
      if (!allowed.includes(selectedTimeframe)) {
        const closest = allowed.reduce((prev, curr) =>
          Math.abs(curr - selectedTimeframe) < Math.abs(prev - selectedTimeframe)
            ? curr
            : prev
        );
        onTimeframeChange(closest);
      }
      onIntervalChange(newInterval);
    },
    [selectedTimeframe, onIntervalChange, onTimeframeChange]
  );

  const handleDateApply = () => {
    if (customStart && customEnd) {
      const startDate = new Date(customStart);
      const endDate = new Date(customEnd);
      const days = Math.ceil(
        (endDate.getTime() - startDate.getTime()) / (1000 * 60 * 60 * 24)
      );
      onTimeframeChange(days);
      setShowAdvanced(false);
      setCustomStart("");
      setCustomEnd("");
      setIsOpen(false);
    }
  };

  // Close modal when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        modalRef.current &&
        buttonRef.current &&
        !modalRef.current.contains(e.target as Node) &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [isOpen]);

  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      {/* Compact Trigger Button */}
      <button
        ref={buttonRef}
        onClick={() => setIsOpen(!isOpen)}
        style={{
          padding: "5px 14px",
          fontSize: "12px",
          fontWeight: "700",
          letterSpacing: "0.06em",
          color: "#f0f0f0",
          background: isOpen ? "#252525" : "#202020",
          border: isOpen ? "1px solid #4a4a4a" : "1px solid #3a3a3a",
          borderRadius: "999px",
          cursor: "pointer",
          fontFamily: "inherit",
          textTransform: "none",
          transition: "all 0.15s ease",
          whiteSpace: "nowrap",
          display: "inline-flex",
          alignItems: "center",
          gap: "7px",
        }}
        onMouseEnter={(e) => {
          if (!isOpen) {
            (e.target as HTMLButtonElement).style.borderColor = "#5a5a5a";
            (e.target as HTMLButtonElement).style.background = "#252525";
          }
        }}
        onMouseLeave={(e) => {
          if (!isOpen) {
            (e.target as HTMLButtonElement).style.borderColor = "#3a3a3a";
            (e.target as HTMLButtonElement).style.background = "#202020";
          }
        }}
      >
        <span>{selectedInterval}</span>
        <span style={{ fontSize: "16px", lineHeight: 1, transform: "translateY(-0.5px)" }}>•</span>
        <span>{selectedTimeframeLabel}</span>
      </button>

      {/* Floating Modal Popup */}
      {isOpen && (
        <div
          ref={modalRef}
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            left: 0,
            background: "#111111",
            border: "1px solid #2a2a2a",
            borderRadius: "8px",
            padding: "12px",
            zIndex: 2000,
            minWidth: "280px",
            boxShadow: "0 10px 40px rgba(0, 0, 0, 0.5)",
          }}
        >
          {/* Interval Selector */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
            <label
              style={{
                fontSize: "10px",
                fontWeight: "700",
                color: "#b0b0b0",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                minWidth: "50px",
              }}
            >
              Interval
            </label>
            <select
              value={selectedInterval}
              onChange={(e) => handleIntervalChange(e.target.value)}
              style={{
                flex: 1,
                padding: "5px 8px",
                fontSize: "11px",
                fontWeight: "600",
                color: "#e8e8e8",
                background: "#1a1a1a",
                border: "1px solid #2a2a2a",
                borderRadius: "3px",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              <option value="1m">1m</option>
              <option value="5m">5m</option>
              <option value="15m">15m</option>
              <option value="30m">30m</option>
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
          </div>

          {/* Timespan Grid */}
          <div style={{ marginBottom: "12px" }}>
            <label
              style={{
                fontSize: "10px",
                fontWeight: "700",
                color: "#b0b0b0",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                display: "block",
                marginBottom: "6px",
              }}
            >
              Timespan
            </label>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(5, 1fr)",
                gap: "3px",
              }}
            >
              {allowedTimespans.map((days) => (
                <button
                  key={days}
                  onClick={() => {
                    onTimeframeChange(days);
                    setIsOpen(false);
                  }}
                  style={{
                    padding: "4px 6px",
                    fontSize: "10px",
                    fontWeight:
                      selectedTimeframe === days ? "700" : "600",
                    color:
                      selectedTimeframe === days
                        ? "#22c55e"
                        : "#8b8d91",
                    background:
                      selectedTimeframe === days
                        ? "rgba(34, 197, 94, 0.2)"
                        : "transparent",
                    border:
                      selectedTimeframe === days
                        ? "1px solid #22c55e"
                        : "1px solid #2a2a2a",
                    borderRadius: "3px",
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                    fontFamily: "inherit",
                  }}
                  onMouseEnter={(e) => {
                    if (selectedTimeframe !== days) {
                      (e.target as HTMLButtonElement).style.borderColor =
                        "#444";
                      (e.target as HTMLButtonElement).style.color =
                        "#cbd5e1";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (selectedTimeframe !== days) {
                      (e.target as HTMLButtonElement).style.borderColor =
                        "#2a2a2a";
                      (e.target as HTMLButtonElement).style.color =
                        "#8b8d91";
                    }
                  }}
                >
                  {TIMESPAN_LABELS[days]}
                </button>
              ))}
            </div>
          </div>

          {/* Advanced Mode Toggle */}
          <div style={{ borderTop: "1px solid #2a2a2a", paddingTop: "8px" }}>
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              style={{
                width: "100%",
                padding: "5px 8px",
                fontSize: "10px",
                fontWeight: "700",
                color: showAdvanced ? "#22c55e" : "#b0b0b0",
                background: showAdvanced
                  ? "rgba(34, 197, 94, 0.15)"
                  : "transparent",
                border: showAdvanced
                  ? "1px solid #22c55e"
                  : "1px solid #2a2a2a",
                borderRadius: "3px",
                cursor: "pointer",
                fontFamily: "inherit",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => {
                if (!showAdvanced) {
                  (e.target as HTMLButtonElement).style.borderColor =
                    "#444";
                  (e.target as HTMLButtonElement).style.color =
                    "#d8d8d8";
                }
              }}
              onMouseLeave={(e) => {
                if (!showAdvanced) {
                  (e.target as HTMLButtonElement).style.borderColor =
                    "#2a2a2a";
                  (e.target as HTMLButtonElement).style.color = "#b0b0b0";
                }
              }}
            >
              📅 Custom Dates
            </button>
          </div>

          {/* Advanced Date Picker */}
          {showAdvanced && (
            <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
              <div style={{ display: "flex", gap: "6px" }}>
                <input
                  type="date"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  style={{
                    flex: 1,
                    padding: "4px 6px",
                    fontSize: "11px",
                    color: "#e8e8e8",
                    background: "#1a1a1a",
                    border: "1px solid #2a2a2a",
                    borderRadius: "3px",
                    fontFamily: "inherit",
                  }}
                />
                <input
                  type="date"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  style={{
                    flex: 1,
                    padding: "4px 6px",
                    fontSize: "11px",
                    color: "#e8e8e8",
                    background: "#1a1a1a",
                    border: "1px solid #2a2a2a",
                    borderRadius: "3px",
                    fontFamily: "inherit",
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: "4px" }}>
                <button
                  onClick={handleDateApply}
                  style={{
                    flex: 1,
                    padding: "5px 8px",
                    fontSize: "10px",
                    fontWeight: "700",
                    color: "#000",
                    background: "#22c55e",
                    border: "none",
                    borderRadius: "3px",
                    cursor: "pointer",
                    fontFamily: "inherit",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    transition: "all 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    (e.target as HTMLButtonElement).style.background =
                      "#1da94a";
                  }}
                  onMouseLeave={(e) => {
                    (e.target as HTMLButtonElement).style.background =
                      "#22c55e";
                  }}
                >
                  Apply
                </button>
                <button
                  onClick={() => {
                    setShowAdvanced(false);
                    setCustomStart("");
                    setCustomEnd("");
                  }}
                  style={{
                    flex: 1,
                    padding: "5px 8px",
                    fontSize: "10px",
                    fontWeight: "700",
                    color: "#8b8d91",
                    background: "transparent",
                    border: "1px solid #2a2a2a",
                    borderRadius: "3px",
                    cursor: "pointer",
                    fontFamily: "inherit",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    transition: "all 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    (e.target as HTMLButtonElement).style.borderColor =
                      "#444";
                    (e.target as HTMLButtonElement).style.color =
                      "#cbd5e1";
                  }}
                  onMouseLeave={(e) => {
                    (e.target as HTMLButtonElement).style.borderColor =
                      "#2a2a2a";
                    (e.target as HTMLButtonElement).style.color =
                      "#8b8d91";
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
