import { useEffect, useLayoutEffect, useRef, useState, useCallback } from "react";
import { createChart, IChartApi, ISeriesApi, Time, ISeriesPrimitive, IPrimitivePaneView, IPrimitivePaneRenderer } from "lightweight-charts";
import { CanvasRenderingTarget2D } from 'fancy-canvas';
import { LineRecord, MarketCandle } from "../types/events";
import type { OHLCBar } from "../history/fetchHistory";
import { formatEasternChartTime, formatEasternDateTime } from "../utils/time";

interface ChartViewProps {
  onCandleReceived: (handler: (candle: MarketCandle) => void) => void;
  onSnapshotRequested: (handler: (bars: OHLCBar[]) => void) => void;
  overlayData: Map<string, LineRecord[]>;
  overlayLineColors?: Map<string, string>;
  selectedSymbol: string;
  selectedInterval?: string;
  selectedTimeframe?: number;
  onTimeframeChange?: (days: number) => void;
  isLeftCollapsed?: boolean;
  isRightCollapsed?: boolean;
  candleType?: "candlestick" | "bar" | "line" | "area";
}

function cssVar(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

function toUnixSeconds(ts: string): number | null {
  const millis = Date.parse(ts);
  if (!Number.isFinite(millis)) {
    return null;
  }

  return Math.floor(millis / 1000);
}

const EASTERN_TIME_ZONE = "America/New_York";

const easternAxisTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const easternAxisDateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  month: "2-digit",
  day: "2-digit",
  hour12: false,
});

function formatEasternAxisTick(time: Time): string {
  if (typeof time === "number") {
    return easternAxisTimeFormatter.format(new Date(time * 1000));
  }

  if (typeof time === "string") {
    const date = new Date(`${time}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return easternAxisDateFormatter.format(date);
  }

  const date = new Date(Date.UTC(time.year, time.month - 1, time.day));
  return easternAxisDateFormatter.format(date);
}

function isExtendedHours(unixSeconds: number): boolean {
  const date = new Date(unixSeconds * 1000);
  const easternTime = new Date(date.toLocaleString("en-US", { timeZone: EASTERN_TIME_ZONE }));
  const hours = easternTime.getHours();
  const minutes = easternTime.getMinutes();
  const totalMinutes = hours * 60 + minutes;
  
  // Market hours: 9:30 AM (570) to 4:00 PM (960)
  const marketOpenMinutes = 9 * 60 + 30;
  const marketCloseMinutes = 16 * 60;
  
  return totalMinutes < marketOpenMinutes || totalMinutes >= marketCloseMinutes;
}

// Extended Hours Shading Primitive
class ExtendedHoursShadeRenderer implements IPrimitivePaneRenderer {
  _chart: IChartApi;
  _shadeColor: string = 'rgba(128, 128, 128, 0.08)';

  constructor(chart: IChartApi) {
    this._chart = chart;
  }

  // Determine if a given UTC date is in Eastern Daylight Time (EDT) or Standard Time (EST)
  // EDT is UTC-4, EST is UTC-5. US DST is roughly 2nd Sunday March to 1st Sunday November
  private isEasternDaylightTime(date: Date): boolean {
    // Check what Eastern time this UTC moment corresponds to by using formatter
    const formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
    
    // Use noon UTC as a test point since it's always unambiguous
    const testDate = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), 12, 0, 0));
    const easternTimeStr = formatter.format(testDate);
    const eastHour = parseInt(easternTimeStr.split(':')[0]);
    
    // UTC noon (12:00) converts to:
    // - 07:00 Eastern in EST (UTC-5)
    // - 08:00 Eastern in EDT (UTC-4)
    return (12 - eastHour) === 4;
  }

  draw(target: CanvasRenderingTarget2D) {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const timeScale = this._chart.timeScale();
      const visibleRange = timeScale.getVisibleRange();
      
      if (!visibleRange) return;

      const startTime = visibleRange.from as number;
      const endTime = visibleRange.to as number;
      const chartHeight = scope.bitmapSize.height;

      // Generate shade regions for visible date range
      const startDate = new Date(startTime * 1000);
      const endDate = new Date(endTime * 1000);
      
      let currentDate = new Date(startDate);
      currentDate.setUTCHours(0, 0, 0, 0);

      ctx.fillStyle = this._shadeColor;

      while (currentDate <= endDate) {
        // Get the correct UTC offset for this date (EDT = 4, EST = 5)
        const isDST = this.isEasternDaylightTime(currentDate);
        const offsetHours = isDST ? 4 : 5;

        // Pre-market: 00:00 - 09:30 Eastern Time
        const preMarketStart = new Date(currentDate);
        preMarketStart.setUTCHours(offsetHours, 0, 0, 0);
        
        const preMarketEnd = new Date(currentDate);
        preMarketEnd.setUTCHours(offsetHours + 9, 30, 0, 0);
        
        const preMarketStartUnix = Math.floor(preMarketStart.getTime() / 1000);
        const preMarketEndUnix = Math.floor(preMarketEnd.getTime() / 1000);

        // After-hours: 16:00 - 24:00 Eastern Time
        const afterHoursStart = new Date(currentDate);
        afterHoursStart.setUTCHours(offsetHours + 16, 0, 0, 0);
        
        const afterHoursEnd = new Date(currentDate);
        afterHoursEnd.setUTCDate(afterHoursEnd.getUTCDate() + 1);
        afterHoursEnd.setUTCHours(offsetHours, 0, 0, 0);
        
        const afterHoursStartUnix = Math.floor(afterHoursStart.getTime() / 1000);
        const afterHoursEndUnix = Math.floor(afterHoursEnd.getTime() / 1000);

        // Draw pre-market
        if (preMarketStartUnix < endTime && preMarketEndUnix > startTime) {
          const x1 = timeScale.timeToCoordinate(Math.max(preMarketStartUnix, startTime));
          const x2 = timeScale.timeToCoordinate(Math.min(preMarketEndUnix, endTime));
          
          if (x1 !== null && x2 !== null) {
            const pixelX1 = Math.round(x1 * scope.horizontalPixelRatio);
            const pixelX2 = Math.round(x2 * scope.horizontalPixelRatio);
            const width = Math.max(1, pixelX2 - pixelX1);
            if (width > 0 && pixelX1 >= -1000 && pixelX1 <= scope.bitmapSize.width + 1000) {
              ctx.fillRect(pixelX1, 0, width, chartHeight);
            }
          }
        }

        // Draw after-hours
        if (afterHoursStartUnix < endTime && afterHoursEndUnix > startTime) {
          const x1 = timeScale.timeToCoordinate(Math.max(afterHoursStartUnix, startTime));
          const x2 = timeScale.timeToCoordinate(Math.min(afterHoursEndUnix, endTime));
          
          if (x1 !== null && x2 !== null) {
            const pixelX1 = Math.round(x1 * scope.horizontalPixelRatio);
            const pixelX2 = Math.round(x2 * scope.horizontalPixelRatio);
            const width = Math.max(1, pixelX2 - pixelX1);
            if (width > 0 && pixelX1 >= -1000 && pixelX1 <= scope.bitmapSize.width + 1000) {
              ctx.fillRect(pixelX1, 0, width, chartHeight);
            }
          }
        }

        currentDate.setUTCDate(currentDate.getUTCDate() + 1);
      }
    });
  }
}

class ExtendedHoursShadePaneView implements IPrimitivePaneView {
  _chart: IChartApi;
  _renderer: ExtendedHoursShadeRenderer;

  constructor(chart: IChartApi) {
    this._chart = chart;
    this._renderer = new ExtendedHoursShadeRenderer(chart);
  }

  update() {
    // Update is called when chart changes
  }

  renderer() {
    return this._renderer;
  }
}

class ExtendedHoursShade implements ISeriesPrimitive<Time> {
  _chart: IChartApi;
  _paneView: ExtendedHoursShadePaneView;

  constructor(chart: IChartApi) {
    this._chart = chart;
    this._paneView = new ExtendedHoursShadePaneView(chart);
  }

  updateAllViews() {
    this._paneView.update();
  }

  paneViews() {
    return [this._paneView];
  }

  timeAxisViews() {
    return [];
  }
}

function normalizeHistoryBars(bars: OHLCBar[]): OHLCBar[] {
  const latestById = new Map<string, OHLCBar>();

  for (const bar of bars) {
    const current = latestById.get(bar.id);
    if (!current || bar.rev > current.rev || (bar.rev === current.rev && bar.bar_state === "final")) {
      latestById.set(bar.id, bar);
    }
  }

  return Array.from(latestById.values()).sort((a, b) => {
    const aTs = Date.parse(a.ts);
    const bTs = Date.parse(b.ts);

    if (!Number.isFinite(aTs) && !Number.isFinite(bTs)) return 0;
    if (!Number.isFinite(aTs)) return 1;
    if (!Number.isFinite(bTs)) return -1;
    if (aTs !== bTs) return aTs - bTs;
    return a.rev - b.rev;
  });
}

export function ChartView({ 
  onCandleReceived,
  onSnapshotRequested,
  overlayData,
  overlayLineColors,
  selectedSymbol, 
  selectedInterval = "1m",
  selectedTimeframe = 1,
  onTimeframeChange,
  isLeftCollapsed, 
  isRightCollapsed,
  candleType = "candlestick"
}: ChartViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<any> | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedCandleDetails, setSelectedCandleDetails] = useState<MarketCandle | null>(null);
  
  // Store candle data by timestamp for lookup
  const candleDataRef = useRef<Map<number, MarketCandle>>(new Map());
  const overlaySeriesRef = useRef<Map<string, ISeriesApi<any>>>(new Map());

  // Handle snapshot data from backend
  const handleSnapshot = useCallback((bars: OHLCBar[]) => {
    console.log("[ChartView] Received snapshot with", bars.length, "bars");
    
    if (!seriesRef.current) {
      console.warn("[ChartView] Series not ready for snapshot");
      return;
    }
    
    setIsLoadingHistory(true);
    setHistoryError(null);
    
    try {
      const normalizedBars = normalizeHistoryBars(bars);

      if (normalizedBars.length === 0 && candleDataRef.current.size > 0) {
        console.warn("[ChartView] Received empty snapshot; preserving existing chart data");
        setHistoryError(null);
        return;
      }
      
      // Clear and rebuild candle data map
      candleDataRef.current.clear();
      
      // Transform data based on chart type
      let chartData: any[] = [];
      
      if (candleType === 'line' || candleType === 'area') {
        // Line and area charts use close price as the value
        chartData = normalizedBars
          .map((bar) => {
            const time = toUnixSeconds(bar.ts);
            if (time === null) return null;
            
            // Store full candle data
            candleDataRef.current.set(time, bar);
            
            return {
              time,
              value: bar.close,
            };
          })
          .filter((bar): bar is { time: number; value: number } => bar !== null);
      } else {
        // Candlestick and bar charts use OHLC data
        chartData = normalizedBars
          .map((bar) => {
            const time = toUnixSeconds(bar.ts);
            if (time === null) return null;
            
            // Store full candle data
            candleDataRef.current.set(time, bar);
            
            return {
              time,
              open: bar.open,
              high: bar.high,
              low: bar.low,
              close: bar.close,
            };
          })
          .filter(
            (
              bar
            ): bar is {
              time: number;
              open: number;
              high: number;
              low: number;
              close: number;
            } => bar !== null
          );
      }
      
      // Deduplicate by timestamp (keep last bar for each unique time)
      // This prevents "data must be asc ordered by time" errors when multiple
      // bars map to the same Unix second timestamp
      const uniqueByTime = new Map<number, any>();
      for (const bar of chartData) {
        uniqueByTime.set(bar.time, bar);
      }
      chartData = Array.from(uniqueByTime.values()).sort((a, b) => a.time - b.time);
      
      seriesRef.current.setData(chartData);
      
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent();
      }
      
      console.log("[ChartView] Chart populated with", chartData.length, "bars");
    } catch (error) {
      console.error("Failed to process snapshot:", error);
      setHistoryError(error instanceof Error ? error.message : "Unknown error");
    } finally {
      setIsLoadingHistory(false);
    }
  }, [candleType]);
  
  // Register snapshot handler
  useEffect(() => {
    onSnapshotRequested(handleSnapshot);
  }, [onSnapshotRequested, handleSnapshot]);

  // Wait for series to be ready, then indicate ready for snapshot
  useEffect(() => {
    let isMounted = true;
    setIsLoadingHistory(true);
    setHistoryError(null);
    
    const waitForSeries = async () => {
      const maxWaitMs = 5000;
      const startWait = Date.now();
      
      while (!seriesRef.current && isMounted && (Date.now() - startWait < maxWaitMs)) {
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      
      if (!isMounted) return;
      
      if (!seriesRef.current) {
        console.error("[ChartView] Series not ready after timeout");
        setHistoryError("Chart initialization timeout");
        setIsLoadingHistory(false);
      } else {
        console.log("[ChartView] Chart series ready, waiting for snapshot...");
        // isLoadingHistory stays true until snapshot arrives
      }
    };
    
    waitForSeries();
    
    return () => {
      isMounted = false;
    };
  }, [selectedSymbol, selectedInterval, selectedTimeframe]);

  // Remove old history loading effect - now using snapshots via WebSocket

  useLayoutEffect(() => {
    if (!containerRef.current) return;

    const chartBackground = cssVar("--chart-bg", "#0f172a");
    const chartText = cssVar("--chart-text", "#cbd5e1");
    const chartGrid = cssVar("--chart-grid", "#1e293b");
    const chartUp = cssVar("--chart-up", "#22c55e");
    const chartDown = cssVar("--chart-down", "#ef4444");

    const chart = createChart(containerRef.current, {
      layout: {
        textColor: chartText,
        background: { color: chartBackground },
      },
      grid: {
        vertLines: { color: chartGrid },
        horzLines: { color: chartGrid },
      },
      localization: {
        timeFormatter: (time: number) => {
          return formatEasternChartTime(time);
        },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 8,
        barSpacing: 7,
        minBarSpacing: 2,
        tickMarkFormatter: (time: Time) => formatEasternAxisTick(time),
      },
      rightPriceScale: {
        borderColor: chartGrid,
      },
      crosshair: {
        vertLine: { color: chartGrid },
        horzLine: { color: chartGrid },
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    // Create the appropriate series type based on candleType
    let series: ISeriesApi<any>;
    
    if (candleType === "bar") {
      series = chart.addBarSeries({
        upColor: chartUp,
        downColor: chartDown,
        priceLineVisible: false,
      });
    } else if (candleType === "line") {
      series = chart.addLineSeries({
        color: chartUp,
        lineWidth: 2,
        priceLineVisible: false,
      });
    } else if (candleType === "area") {
      series = chart.addAreaSeries({
        topColor: `${chartUp}40`,
        bottomColor: `${chartUp}05`,
        lineColor: chartUp,
        lineWidth: 2,
        priceLineVisible: false,
      });
    } else {
      // Default to candlestick
      series = chart.addCandlestickSeries({
        upColor: chartUp,
        downColor: chartDown,
        borderVisible: true,
        wickUpColor: chartUp,
        wickDownColor: chartDown,
        priceLineVisible: false,
      });
    }

    // Attach extended hours shading primitive
    const extendedHoursShade = new ExtendedHoursShade(chart);
    series.attachPrimitive(extendedHoursShade);

    chartRef.current = chart;
    seriesRef.current = series;

    const handleCandle = (candle: MarketCandle) => {
      if (!seriesRef.current) return;

      const time = toUnixSeconds(candle.ts);
      if (time === null) return;

      const hadNoCandles = candleDataRef.current.size === 0;
      
      // Store the candle data
      candleDataRef.current.set(time, candle);
      
      // Format data based on chart type
      if (candleType === 'line' || candleType === 'area') {
        seriesRef.current.update({
          time,
          value: candle.close,
        });
      } else {
        seriesRef.current.update({
          time,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        });
      }

      if (hadNoCandles && chartRef.current) {
        chartRef.current.timeScale().fitContent();
      }
    };

    onCandleReceived(handleCandle);

    // Double-click handler to show candle details
    let clickedCandleTime: number | null = null;

    const handleChartClick = (param: any) => {
      if (!param.time) {
        clickedCandleTime = null;
        return;
      }
      
      clickedCandleTime = param.time as number;
    };

    chart.subscribeClick(handleChartClick);

    const handleDoubleClick = () => {
      if (clickedCandleTime !== null) {
        const candleData = candleDataRef.current.get(clickedCandleTime);
        if (candleData) {
          setSelectedCandleDetails(candleData);
        }
      }
    };

    containerRef.current.addEventListener('dblclick', handleDoubleClick);

    // Right-click context menu handler
    const handleContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      
      // Remove any existing context menu
      const existingMenu = document.querySelector('.chart-context-menu');
      if (existingMenu) {
        existingMenu.remove();
      }

      // Create context menu
      const menu = document.createElement('div');
      menu.className = 'chart-context-menu';
      menu.style.position = 'fixed';
      menu.style.top = e.clientY + 'px';
      menu.style.left = e.clientX + 'px';
      menu.style.background = '#1a1a1a';
      menu.style.border = '1px solid #333';
      menu.style.borderRadius = '4px';
      menu.style.minWidth = '150px';
      menu.style.zIndex = '10000';
      menu.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.5)';

      // Show Candle Details option
      if (clickedCandleTime !== null) {
        const candleData = candleDataRef.current.get(clickedCandleTime);
        if (candleData) {
          const showDetailsOption = document.createElement('button');
          showDetailsOption.textContent = 'Show Candle Details';
          showDetailsOption.style.display = 'block';
          showDetailsOption.style.width = '100%';
          showDetailsOption.style.padding = '8px 12px';
          showDetailsOption.style.background = 'none';
          showDetailsOption.style.border = 'none';
          showDetailsOption.style.color = '#e0e0e0';
          showDetailsOption.style.fontSize = '12px';
          showDetailsOption.style.textAlign = 'left';
          showDetailsOption.style.cursor = 'pointer';
          showDetailsOption.style.fontFamily = 'inherit';
          showDetailsOption.style.transition = 'background 0.15s';

          showDetailsOption.onmouseenter = () => {
            showDetailsOption.style.background = 'rgba(34, 197, 94, 0.2)';
          };

          showDetailsOption.onmouseleave = () => {
            showDetailsOption.style.background = 'none';
          };

          showDetailsOption.onclick = () => {
            setSelectedCandleDetails(candleData);
            menu.remove();
          };

          menu.appendChild(showDetailsOption);
        }
      }

      const resetOption = document.createElement('button');
      resetOption.textContent = 'Reset Chart';
      resetOption.style.display = 'block';
      resetOption.style.width = '100%';
      resetOption.style.padding = '8px 12px';
      resetOption.style.background = 'none';
      resetOption.style.border = 'none';
      resetOption.style.color = '#e0e0e0';
      resetOption.style.fontSize = '12px';
      resetOption.style.textAlign = 'left';
      resetOption.style.cursor = 'pointer';
      resetOption.style.fontFamily = 'inherit';
      resetOption.style.transition = 'background 0.15s';

      resetOption.onmouseenter = () => {
        resetOption.style.background = 'rgba(34, 197, 94, 0.2)';
      };

      resetOption.onmouseleave = () => {
        resetOption.style.background = 'none';
      };

      resetOption.onclick = () => {
        if (chartRef.current) {
          chartRef.current.timeScale().fitContent();
        }
        menu.remove();
      };

      menu.appendChild(resetOption);
      document.body.appendChild(menu);

      // Close menu when clicking elsewhere
      const closeMenu = () => {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      };

      setTimeout(() => {
        document.addEventListener('click', closeMenu);
      }, 0);
    };

    containerRef.current.addEventListener('contextmenu', handleContextMenu);

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        const width = containerRef.current.clientWidth;
        const height = containerRef.current.clientHeight;
        
        if (width > 0 && height > 0) {
          chartRef.current.applyOptions({ width, height });
        }
      }
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerRef.current);

    // Also observe parent containers to catch grid layout changes
    const chartStage = containerRef.current.closest('.chart-stage');
    if (chartStage) {
      resizeObserver.observe(chartStage);
    }

    // Trigger initial resize after a frame to ensure layout is settled
    requestAnimationFrame(() => {
      handleResize();
    });

    return () => {
      resizeObserver.disconnect();
      if (containerRef.current) {
        containerRef.current.removeEventListener('contextmenu', handleContextMenu);
        containerRef.current.removeEventListener('dblclick', handleDoubleClick);
      }
      if (chart) {
        chart.unsubscribeClick(handleChartClick);
        chart.remove();
      }
      // Null out refs so history loading knows the chart was destroyed
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [onCandleReceived, candleType]);

  // Force chart resize when panels collapse/expand
  useEffect(() => {
    if (!containerRef.current || !chartRef.current) {
      return;
    }

    // Use multiple resize attempts to ensure the chart catches the final dimensions
    const resizeChart = () => {
      if (containerRef.current && chartRef.current) {
        const width = containerRef.current.clientWidth;
        const height = containerRef.current.clientHeight;
        
        if (width > 0 && height > 0) {
          chartRef.current.applyOptions({ width, height });
        }
      }
    };

    // Resize immediately
    resizeChart();

    // Then resize after the CSS transition completes (280ms)
    const timeoutId1 = setTimeout(resizeChart, 300);
    
    // And one more time to be sure
    const timeoutId2 = setTimeout(resizeChart, 350);

    return () => {
      clearTimeout(timeoutId1);
      clearTimeout(timeoutId2);
    };
  }, [isLeftCollapsed, isRightCollapsed]);

  useEffect(() => {
    if (!chartRef.current) {
      return;
    }

    try {
      const chart = chartRef.current;
      const existingSeries = overlaySeriesRef.current;
      const incomingAgentIds = new Set(overlayData.keys());

      console.log("[ChartView] Updating overlays:", {
        overlayDataKeys: Array.from(overlayData.keys()),
        overlayColorKeys: overlayLineColors ? Array.from(overlayLineColors.keys()) : [],
        existingSeriesKeys: Array.from(existingSeries.keys())
      });

      for (const [agentId, overlaySeries] of existingSeries.entries()) {
        if (!incomingAgentIds.has(agentId)) {
          console.log(`[ChartView] Removing overlay series for ${agentId}`);
          chart.removeSeries(overlaySeries);
          existingSeries.delete(agentId);
        }
      }

      for (const [agentId, records] of overlayData.entries()) {
        try {
          let overlaySeries = existingSeries.get(agentId);
          const seriesColor = overlayLineColors?.get(agentId) || cssVar("--chart-text", "#cbd5e1");
          console.log(`[ChartView] Processing overlay for ${agentId}: color=${seriesColor}, records=${records.length}`);
          
          if (!overlaySeries) {
            console.log(`[ChartView] Creating new line series for ${agentId} with color ${seriesColor}`);
            overlaySeries = chart.addLineSeries({
              color: seriesColor,
              lineWidth: 2,
              priceLineVisible: false,
              lastValueVisible: false,
            });
            existingSeries.set(agentId, overlaySeries);
          } else {
            console.log(`[ChartView] Updating existing series for ${agentId} with color ${seriesColor}`);
            overlaySeries.applyOptions({ color: seriesColor });
          }

          const lineData = records
            .map((record) => {
              const time = toUnixSeconds(record.ts);
              const value = Number(record.value);
              if (time === null || !Number.isFinite(value)) {
                return null;
              }

              return {
                time,
                value,
              };
            })
            .filter((point): point is { time: number; value: number } => point !== null)
            .sort((a, b) => a.time - b.time);

          const uniqueLineDataByTime = new Map<number, { time: number; value: number }>();
          for (const point of lineData) {
            uniqueLineDataByTime.set(point.time, point);
          }

          const deduplicatedLineData = Array.from(uniqueLineDataByTime.values()).sort(
            (a, b) => a.time - b.time
          );

          if (deduplicatedLineData.length > 0) {
            overlaySeries.setData(deduplicatedLineData);
            console.log(`[ChartView] Set ${deduplicatedLineData.length} data points for ${agentId}`);
          }
        } catch (error) {
          console.error(`[ChartView] Failed to render overlay for agent ${agentId}:`, error);
        }
      }
    } catch (error) {
      console.error("[ChartView] Failed to update overlay series:", error);
    }
  }, [overlayData, overlayLineColors]);

  const timeframeOptions = [
    { label: "1D", days: 1 },
    { label: "1W", days: 7 },
    { label: "1M", days: 30 },
    { label: "3M", days: 90 },
    { label: "6M", days: 180 },
    { label: "1Y", days: 365 },
  ];

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      {/* Loading/Error Indicator */}
      {(isLoadingHistory || historyError) && (
        <div
          style={{
            position: "absolute",
            top: "12px",
            right: "12px",
            zIndex: 10,
            padding: "8px 12px",
            background: "rgba(15, 23, 42, 0.9)",
            border: `1px solid ${historyError ? "#ef4444" : "rgba(148, 163, 184, 0.2)"}`,
            borderRadius: "6px",
            fontSize: "12px",
            color: historyError ? "#ef4444" : "#cbd5e1",
          }}
        >
          {isLoadingHistory && "Loading history..."}
          {historyError && `Error: ${historyError}`}
        </div>
      )}

      {/* Candle Details Modal */}
      {selectedCandleDetails && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.7)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 10001,
          }}
          onClick={() => setSelectedCandleDetails(null)}
        >
          <div
            style={{
              background: "#1a1a1a",
              border: "1px solid #333",
              borderRadius: "8px",
              padding: "20px",
              minWidth: "400px",
              maxWidth: "500px",
              boxShadow: "0 8px 32px rgba(0, 0, 0, 0.5)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "16px",
                borderBottom: "1px solid #333",
                paddingBottom: "12px",
              }}
            >
              <h3 style={{ margin: 0, color: "#e0e0e0", fontSize: "16px", fontWeight: 600 }}>
                Candle Details
              </h3>
              <button
                onClick={() => setSelectedCandleDetails(null)}
                style={{
                  background: "none",
                  border: "none",
                  color: "#999",
                  fontSize: "20px",
                  cursor: "pointer",
                  padding: "0 4px",
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </div>
            
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Timestamp:</span>
                <span style={{ color: "#e0e0e0", fontSize: "13px", fontFamily: "monospace" }}>
                  {formatEasternDateTime(selectedCandleDetails.ts)}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>ID:</span>
                <span style={{ color: "#e0e0e0", fontSize: "13px", fontFamily: "monospace" }}>
                  {selectedCandleDetails.id}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>State:</span>
                <span style={{ 
                  color: selectedCandleDetails.bar_state === 'final' ? '#22c55e' : '#f59e0b', 
                  fontSize: "13px",
                  fontWeight: 500
                }}>
                  {selectedCandleDetails.bar_state.toUpperCase()}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Revision:</span>
                <span style={{ color: "#e0e0e0", fontSize: "13px", fontFamily: "monospace" }}>
                  {selectedCandleDetails.rev}
                </span>
              </div>
              
              <div style={{ height: "1px", background: "#333", margin: "6px 0" }} />
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Open:</span>
                <span style={{ color: "#e0e0e0", fontSize: "14px", fontWeight: 600, fontFamily: "monospace" }}>
                  ${selectedCandleDetails.open.toFixed(2)}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>High:</span>
                <span style={{ color: "#22c55e", fontSize: "14px", fontWeight: 600, fontFamily: "monospace" }}>
                  ${selectedCandleDetails.high.toFixed(2)}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Low:</span>
                <span style={{ color: "#ef4444", fontSize: "14px", fontWeight: 600, fontFamily: "monospace" }}>
                  ${selectedCandleDetails.low.toFixed(2)}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Close:</span>
                <span style={{ 
                  color: selectedCandleDetails.close >= selectedCandleDetails.open ? '#22c55e' : '#ef4444', 
                  fontSize: "14px", 
                  fontWeight: 600,
                  fontFamily: "monospace"
                }}>
                  ${selectedCandleDetails.close.toFixed(2)}
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Volume:</span>
                <span style={{ color: "#60a5fa", fontSize: "14px", fontWeight: 600, fontFamily: "monospace" }}>
                  {selectedCandleDetails.volume.toLocaleString()}
                </span>
              </div>
              
              <div style={{ height: "1px", background: "#333", margin: "6px 0" }} />
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Change:</span>
                <span style={{ 
                  color: selectedCandleDetails.close >= selectedCandleDetails.open ? '#22c55e' : '#ef4444', 
                  fontSize: "13px",
                  fontFamily: "monospace"
                }}>
                  {((selectedCandleDetails.close - selectedCandleDetails.open) >= 0 ? '+' : '')}
                  ${(selectedCandleDetails.close - selectedCandleDetails.open).toFixed(2)} 
                  ({((selectedCandleDetails.close - selectedCandleDetails.open) / selectedCandleDetails.open * 100).toFixed(2)}%)
                </span>
              </div>
              
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Range:</span>
                <span style={{ color: "#e0e0e0", fontSize: "13px", fontFamily: "monospace" }}>
                  ${(selectedCandleDetails.high - selectedCandleDetails.low).toFixed(2)}
                </span>
              </div>
            </div>
            
            <div style={{ marginTop: "16px", paddingTop: "12px", borderTop: "1px solid #333" }}>
              <button
                onClick={() => setSelectedCandleDetails(null)}
                style={{
                  width: "100%",
                  padding: "8px",
                  background: "rgba(34, 197, 94, 0.15)",
                  border: "1px solid rgba(34, 197, 94, 0.3)",
                  borderRadius: "4px",
                  color: "#22c55e",
                  fontSize: "13px",
                  cursor: "pointer",
                  fontWeight: 500,
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(34, 197, 94, 0.25)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "rgba(34, 197, 94, 0.15)";
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Chart Canvas */}
      <div ref={containerRef} className="chart-canvas" />
    </div>
  );
}
