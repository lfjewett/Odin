import { useEffect, useLayoutEffect, useRef, useState, useCallback, useMemo } from "react";
import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  LineSeries,
  SeriesMarker,
  Time,
} from "lightweight-charts";
import { CanvasRenderingTarget2D } from 'fancy-canvas';
import { AreaRecord, MarketCandle, OverlayRecord, OverlaySchema } from "../types/events";
import type { OHLCBar } from "../history/fetchHistory";
import { formatEasternChartTime, formatEasternDateTime } from "../utils/time";
import type { TradeMarker } from "../workspace/workspaceApi";

interface ChartViewProps {
  onCandleReceived: (handler: (candle: MarketCandle) => void) => void;
  onSnapshotRequested: (handler: (bars: OHLCBar[]) => void) => void;
  overlayData: Map<string, OverlayRecord[]>;
  overlaySchemas?: Map<string, OverlaySchema>;
  overlayLineColors?: Map<string, string>;
  overlayAreaStyles?: Map<
    string,
    {
      useSourceStyle: boolean;
      showLabels: boolean;
      fillMode: "solid" | "conditional";
      opacityPercent: number;
      conditionalUpColor?: string;
      conditionalDownColor?: string;
    }
  >;
  /**
   * UI-level sub-graph override map: agentId → true.
   * When an agentId is present and set to true the overlay is forced into a
   * dedicated sub-graph pane regardless of what the agent emits in metadata.
   */
  overlaySubgraphForced?: Map<string, boolean>;
  tradeMarkers?: TradeMarker[];
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

function overlayAgentId(seriesKey: string): string {
  const [agentId] = seriesKey.split("::");
  return agentId || seriesKey;
}

type ChartCompatApi = IChartApi<Time> & {
  addSeries?: (definition: any, options?: any, paneIndex?: number) => ISeriesApi<any>;
  addAreaSeries?: (options?: any) => ISeriesApi<any>;
  addBarSeries?: (options?: any) => ISeriesApi<any>;
  addCandlestickSeries?: (options?: any) => ISeriesApi<any>;
  addHistogramSeries?: (options?: any) => ISeriesApi<any>;
  addLineSeries?: (options?: any) => ISeriesApi<any>;
};

function splitPaneOptions(options?: Record<string, unknown>): {
  normalizedOptions: Record<string, unknown>;
  paneIndex?: number;
} {
  if (!options || typeof options !== "object") {
    return { normalizedOptions: {}, paneIndex: undefined };
  }

  const { pane, ...rest } = options as Record<string, unknown> & { pane?: unknown };
  const paneIndex = typeof pane === "number" ? pane : undefined;
  return { normalizedOptions: rest, paneIndex };
}

function addLineSeriesCompat(chart: ChartCompatApi, options?: Record<string, unknown>): ISeriesApi<any> {
  const { normalizedOptions, paneIndex } = splitPaneOptions(options);
  if (typeof chart.addSeries === "function") {
    return chart.addSeries(LineSeries as any, normalizedOptions as any, paneIndex);
  }
  return chart.addLineSeries!(options as any);
}

function addAreaSeriesCompat(chart: ChartCompatApi, options?: Record<string, unknown>): ISeriesApi<any> {
  const { normalizedOptions, paneIndex } = splitPaneOptions(options);
  if (typeof chart.addSeries === "function") {
    return chart.addSeries(AreaSeries as any, normalizedOptions as any, paneIndex);
  }
  return chart.addAreaSeries!(options as any);
}

function addBarSeriesCompat(chart: ChartCompatApi, options?: Record<string, unknown>): ISeriesApi<any> {
  const { normalizedOptions, paneIndex } = splitPaneOptions(options);
  if (typeof chart.addSeries === "function") {
    return chart.addSeries(BarSeries as any, normalizedOptions as any, paneIndex);
  }
  return chart.addBarSeries!(options as any);
}

function addCandlestickSeriesCompat(chart: ChartCompatApi, options?: Record<string, unknown>): ISeriesApi<any> {
  const { normalizedOptions, paneIndex } = splitPaneOptions(options);
  if (typeof chart.addSeries === "function") {
    return chart.addSeries(CandlestickSeries as any, normalizedOptions as any, paneIndex);
  }
  return chart.addCandlestickSeries!(options as any);
}

function addHistogramSeriesCompat(chart: ChartCompatApi, options?: Record<string, unknown>): ISeriesApi<any> {
  const { normalizedOptions, paneIndex } = splitPaneOptions(options);
  if (typeof chart.addSeries === "function") {
    return chart.addSeries(HistogramSeries as any, normalizedOptions as any, paneIndex);
  }
  return chart.addHistogramSeries!(options as any);
}

interface AreaGradientStyle {
  enabled?: boolean;
  direction?: "vertical" | "horizontal";
  start_color?: string;
  end_color?: string;
}

interface AreaRenderStyle {
  fillMode: "solid" | "conditional";
  primaryColor: string;
  secondaryColor: string;
  fillAlpha: number;
  gradient?: AreaGradientStyle;
}

interface AreaPoint {
  time: number;
  upper: number;
  lower: number;
  style?: AreaRenderStyle;
}

function intervalToSeconds(interval?: string): number | null {
  if (!interval) {
    return null;
  }

  const match = /^(\d+)(m|h|d|w|M)$/i.exec(interval.trim());
  if (!match) {
    return null;
  }

  const value = Number.parseInt(match[1], 10);
  const unit = match[2];
  if (!Number.isFinite(value) || value <= 0) {
    return null;
  }

  switch (unit) {
    case "m":
      return value * 60;
    case "h":
      return value * 3600;
    case "d":
      return value * 86400;
    case "w":
      return value * 604800;
    case "M":
      return value * 2592000;
    default:
      return null;
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function readAreaLabel(record: OverlayRecord | undefined): string | null {
  const metadata = record?.metadata;
  if (!metadata || typeof metadata !== "object") {
    return null;
  }

  const value = (metadata as Record<string, unknown>).label;
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Safe min/max for large arrays. Avoids spread operator which causes stack overflow
 * with >65k items. Used for 231k+ minute candles in 1Y timeframes.
 */
function findMin(values: number[]): number | null {
  if (values.length === 0) return null;
  let result = values[0];
  for (let i = 1; i < values.length; i++) {
    if (values[i] < result) result = values[i];
  }
  return result;
}

function findMax(values: number[]): number | null {
  if (values.length === 0) return null;
  let result = values[0];
  for (let i = 1; i < values.length; i++) {
    if (values[i] > result) result = values[i];
  }
  return result;
}

function normalizeLogicalRange(
  range: { from: number; to: number } | null | undefined
): { from: number; to: number } | null {
  if (!range) {
    return null;
  }

  const { from, to } = range;
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) {
    return null;
  }

  return { from, to };
}

function readAreaRenderStyle(
  record: OverlayRecord | undefined,
  fallbackColor: string,
  uiStyle?: {
    useSourceStyle: boolean;
    showLabels: boolean;
    fillMode: "solid" | "conditional";
    opacityPercent: number;
    conditionalUpColor?: string;
    conditionalDownColor?: string;
  },
  conditionalUpColor: string = "#22c55e",
  conditionalDownColor: string = "#ef4444",
): AreaRenderStyle {
  const metadata = record?.metadata;
  const render = (metadata && typeof metadata === "object"
    ? (metadata as Record<string, unknown>).render
    : undefined) as Record<string, unknown> | undefined;

  const useSourceStyle = uiStyle?.useSourceStyle === true;
  const overrideStyle = useSourceStyle ? undefined : uiStyle;

  const fillMode = overrideStyle?.fillMode === "solid"
    ? "solid"
    : useSourceStyle
      ? (render?.fill_mode === "conditional" ? "conditional" : "solid")
      : (render?.fill_mode === "solid" ? "solid" : "conditional");

  const hasPrimaryColor = typeof render?.primary_color === "string" && render.primary_color.trim().length > 0;
  const hasSecondaryColor = typeof render?.secondary_color === "string" && render.secondary_color.trim().length > 0;
  const primaryColorRaw = hasPrimaryColor ? String(render?.primary_color).trim() : fallbackColor;
  const secondaryColorRaw = hasSecondaryColor ? String(render?.secondary_color).trim() : "";

  const hasOpacity = Number.isFinite(Number(render?.opacity));
  const hasTransparency = Number.isFinite(Number(render?.transparency));
  const opacity = hasOpacity ? clamp(Number(render?.opacity), 0, 1) : 1;
  const transparency = hasTransparency ? clamp(Number(render?.transparency), 0, 100) : 0;

  const metadataAlpha = clamp(opacity * (1 - transparency / 100), 0, 1);
  const fillAlpha = overrideStyle
    ? clamp(overrideStyle.opacityPercent / 100, 0, 1)
    : (hasOpacity || hasTransparency ? metadataAlpha : 0.5);

  const resolvedUpColor = overrideStyle?.conditionalUpColor || conditionalUpColor;
  const resolvedDownColor = overrideStyle?.conditionalDownColor || conditionalDownColor;

  const primaryColor = useSourceStyle
    ? (primaryColorRaw || fallbackColor)
    : (fillMode === "conditional" ? resolvedUpColor : (primaryColorRaw || fallbackColor));
  const secondaryColor = useSourceStyle
    ? (secondaryColorRaw || primaryColor)
    : (fillMode === "conditional" ? resolvedDownColor : (secondaryColorRaw || primaryColor));

  const gradientRaw = render?.gradient;
  const gradient = gradientRaw && typeof gradientRaw === "object"
    ? {
        enabled: Boolean((gradientRaw as Record<string, unknown>).enabled),
        direction: ((gradientRaw as Record<string, unknown>).direction === "horizontal" ? "horizontal" : "vertical") as "vertical" | "horizontal",
        start_color: typeof (gradientRaw as Record<string, unknown>).start_color === "string"
          ? String((gradientRaw as Record<string, unknown>).start_color)
          : undefined,
        end_color: typeof (gradientRaw as Record<string, unknown>).end_color === "string"
          ? String((gradientRaw as Record<string, unknown>).end_color)
          : undefined,
      }
    : undefined;

  return {
    fillMode,
    primaryColor,
    secondaryColor,
    fillAlpha,
    gradient,
  };
}

class AreaBetweenRenderer implements IPrimitivePaneRenderer {
  private chart: IChartApi;
  private upperSeries: ISeriesApi<any>;
  private points: AreaPoint[] = [];
  private style: AreaRenderStyle;
  private maxGapSeconds: number | null = null;

  constructor(chart: IChartApi, upperSeries: ISeriesApi<any>, style: AreaRenderStyle) {
    this.chart = chart;
    this.upperSeries = upperSeries;
    this.style = style;
  }

  update(points: AreaPoint[], style: AreaRenderStyle, maxGapSeconds?: number | null) {
    this.points = points;
    this.style = style;
    this.maxGapSeconds = typeof maxGapSeconds === "number" && Number.isFinite(maxGapSeconds)
      ? maxGapSeconds
      : null;
  }

  draw(target: CanvasRenderingTarget2D) {
    if (this.points.length < 2) {
      return;
    }

    const timeScale = this.chart.timeScale();

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;

      for (let index = 1; index < this.points.length; index += 1) {
        const left = this.points[index - 1];
        const right = this.points[index];

        if (this.maxGapSeconds !== null && right.time - left.time > this.maxGapSeconds) {
          continue;
        }

        const segmentStyle = right.style || left.style || this.style;
        const alpha = clamp(segmentStyle.fillAlpha, 0, 1);
        if (alpha <= 0) {
          continue;
        }

        const x1 = timeScale.timeToCoordinate(left.time);
        const x2 = timeScale.timeToCoordinate(right.time);

        if (x1 === null || x2 === null) {
          continue;
        }

        const yUpper1 = this.upperSeries.priceToCoordinate(left.upper);
        const yUpper2 = this.upperSeries.priceToCoordinate(right.upper);
        const yLower1 = this.upperSeries.priceToCoordinate(left.lower);
        const yLower2 = this.upperSeries.priceToCoordinate(right.lower);

        if (
          yUpper1 === null ||
          yUpper2 === null ||
          yLower1 === null ||
          yLower2 === null
        ) {
          continue;
        }

        const segmentMidUpper = (left.upper + right.upper) / 2;
        const segmentMidLower = (left.lower + right.lower) / 2;
        const isInverted = segmentMidUpper < segmentMidLower;

        const baseColor = segmentStyle.fillMode === "solid"
          ? segmentStyle.primaryColor
          : (isInverted ? segmentStyle.secondaryColor : segmentStyle.primaryColor);

        if (!baseColor) {
          continue;
        }

        const px1 = Math.round(x1 * scope.horizontalPixelRatio);
        const px2 = Math.round(x2 * scope.horizontalPixelRatio);
        const pyUpper1 = Math.round(yUpper1 * scope.verticalPixelRatio);
        const pyUpper2 = Math.round(yUpper2 * scope.verticalPixelRatio);
        const pyLower1 = Math.round(yLower1 * scope.verticalPixelRatio);
        const pyLower2 = Math.round(yLower2 * scope.verticalPixelRatio);

        ctx.save();
        ctx.globalAlpha = alpha;

        if (
          segmentStyle.gradient?.enabled &&
          segmentStyle.gradient.start_color &&
          segmentStyle.gradient.end_color
        ) {
          const vertical = segmentStyle.gradient.direction !== "horizontal";
          const gx1 = vertical ? px1 : px1;
          const gy1 = vertical ? Math.min(pyUpper1, pyUpper2, pyLower1, pyLower2) : pyUpper1;
          const gx2 = vertical ? px1 : px2;
          const gy2 = vertical ? Math.max(pyUpper1, pyUpper2, pyLower1, pyLower2) : pyUpper1;
          const gradient = ctx.createLinearGradient(gx1, gy1, gx2, gy2);
          gradient.addColorStop(0, segmentStyle.gradient.start_color);
          gradient.addColorStop(1, segmentStyle.gradient.end_color);
          ctx.fillStyle = gradient;
        } else {
          ctx.fillStyle = baseColor;
        }

        ctx.beginPath();
        ctx.moveTo(px1, pyUpper1);
        ctx.lineTo(px2, pyUpper2);
        ctx.lineTo(px2, pyLower2);
        ctx.lineTo(px1, pyLower1);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
    });
  }
}

class AreaBetweenPaneView implements IPrimitivePaneView {
  private rendererInstance: AreaBetweenRenderer;

  constructor(chart: IChartApi, upperSeries: ISeriesApi<any>, style: AreaRenderStyle) {
    this.rendererInstance = new AreaBetweenRenderer(chart, upperSeries, style);
  }

  update(points: AreaPoint[], style: AreaRenderStyle, maxGapSeconds?: number | null) {
    this.rendererInstance.update(points, style, maxGapSeconds);
  }

  renderer() {
    return this.rendererInstance;
  }
}

class AreaBetweenPrimitive implements ISeriesPrimitive<Time> {
  private paneView: AreaBetweenPaneView;

  constructor(chart: IChartApi, upperSeries: ISeriesApi<any>, style: AreaRenderStyle) {
    this.paneView = new AreaBetweenPaneView(chart, upperSeries, style);
  }

  update(points: AreaPoint[], style: AreaRenderStyle, maxGapSeconds?: number | null) {
    this.paneView.update(points, style, maxGapSeconds);
  }

  updateAllViews() {
    // no-op
  }

  paneViews() {
    return [this.paneView];
  }

  timeAxisViews() {
    return [];
  }
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

/**
 * Returns true when the overlay should be rendered in a separate sub-graph pane
 * rather than overlaid on the main price chart.
 *
 * Priority order:
 *   1. UI-level force override (overlaySubgraphForced.get(agentId) === true)
 *   2. schema === "histogram" (always a sub-graph)
 *   3. first record's metadata.subgraph === true  (agent opt-in, e.g. ATR)
 */
function isSubgraphOverlay(
  schema: OverlaySchema,
  records: OverlayRecord[],
  agentId: string,
  forcedOverrides?: Map<string, boolean>
): boolean {
  if (forcedOverrides?.get(agentId) === true) return true;
  if (schema === "histogram") return true;
  const firstRecord = records[0];
  if (
    firstRecord?.metadata &&
    (firstRecord.metadata as Record<string, unknown>)["subgraph"] === true
  ) {
    return true;
  }
  return false;
}

function readOverlayColor(record: OverlayRecord | undefined): string | null {
  const metadata = record?.metadata as Record<string, unknown> | undefined;
  const candidate = metadata?.color;
  if (typeof candidate !== "string") {
    return null;
  }
  const trimmed = candidate.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function hasTrendColorMode(records: OverlayRecord[]): boolean {
  const metadata = records[0]?.metadata as Record<string, unknown> | undefined;
  return metadata?.color_mode === "trend";
}

function resolveSubgraphPaneKey(
  schema: OverlaySchema,
  records: OverlayRecord[],
  agentId: string,
  seriesKey: string,
): string {
  const metadata = records[0]?.metadata as Record<string, unknown> | undefined;
  const group = metadata?.subgraph_group;
  if (typeof group === "string" && group.trim().length > 0) {
    return `${agentId}::subgraph::${group.trim()}`;
  }

  if (agentId === "__research__") {
    return `${agentId}::subgraph::1`;
  }

  if (schema === "histogram") {
    return `${seriesKey}::hist`;
  }

  return seriesKey;
}

export function ChartView({ 
  onCandleReceived,
  onSnapshotRequested,
  overlayData,
  overlaySchemas,
  overlayLineColors,
  overlayAreaStyles,
  overlaySubgraphForced,
  tradeMarkers = [],
  selectedSymbol, 
  selectedInterval = "1m",
  selectedTimeframe = 1,
  onTimeframeChange,
  isLeftCollapsed, 
  isRightCollapsed,
  candleType = "candlestick"
}: ChartViewProps) {
  const SUBGRAPH_PANE_STRETCH_FACTOR = 0.5;
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<any> | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedCandleDetails, setSelectedCandleDetails] = useState<MarketCandle | null>(null);
  const [candleWindowRevision, setCandleWindowRevision] = useState(0);
  const [historyBarCount, setHistoryBarCount] = useState(0);
  const [visibleLogicalRange, setVisibleLogicalRange] = useState<{ from: number; to: number } | null>(null);
  const [isScrubberActive, setIsScrubberActive] = useState(false);

  const selectedIndicatorRows = useMemo(() => {
    if (!selectedCandleDetails) {
      return [] as Array<{ seriesName: string; field: string; value: number }>;
    }

    const selectedUnix = toUnixSeconds(selectedCandleDetails.ts);
    if (selectedUnix === null) {
      return [] as Array<{ seriesName: string; field: string; value: number }>;
    }

    const excludedKeys = new Set(["id", "ts", "seq", "rev", "output_id", "metadata"]);
    const rows: Array<{ seriesName: string; field: string; value: number }> = [];

    for (const [seriesKey, records] of overlayData.entries()) {
      if (!records || records.length === 0) continue;

      const parts = seriesKey.split("::");
      const seriesName = parts.length >= 3
        ? `${parts[0]}:${parts[2] === "default" ? parts[1] : parts[2]}`
        : seriesKey;

      const matchingRecord = records.find((record) => {
        const recordUnix = toUnixSeconds(String(record.ts || ""));
        return recordUnix !== null && recordUnix === selectedUnix;
      });

      if (!matchingRecord) continue;

      for (const [field, rawValue] of Object.entries(matchingRecord as Record<string, unknown>)) {
        if (excludedKeys.has(field)) continue;
        if (typeof rawValue === "number" && Number.isFinite(rawValue)) {
          rows.push({ seriesName, field, value: rawValue });
        }
      }

      const metadata = matchingRecord.metadata;
      if (metadata && typeof metadata === "object") {
        for (const [metadataKey, metadataValue] of Object.entries(metadata as Record<string, unknown>)) {
          if (typeof metadataValue === "number" && Number.isFinite(metadataValue)) {
            rows.push({
              seriesName,
              field: `metadata.${metadataKey}`,
              value: metadataValue,
            });
          }
        }
      }
    }

    return rows.sort((a, b) => {
      const bySeries = a.seriesName.localeCompare(b.seriesName);
      if (bySeries !== 0) return bySeries;
      return a.field.localeCompare(b.field);
    });
  }, [selectedCandleDetails, overlayData]);
  
  // Store candle data by timestamp for lookup
  const candleDataRef = useRef<Map<number, MarketCandle>>(new Map());
  const overlayLineSeriesRef = useRef<Map<string, ISeriesApi<any>>>(new Map());
  const overlayAreaSeriesRef = useRef<
    Map<
      string,
      {
        upperSeries: ISeriesApi<any>;
        lowerSeries: ISeriesApi<any>;
        primitive: AreaBetweenPrimitive;
      }
    >
  >(new Map());
  const areaSeriesMarkersRef = useRef<Map<string, ISeriesMarkersPluginApi<Time>>>(new Map());
  const seriesMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const seriesMarkersSeriesRef = useRef<ISeriesApi<any> | null>(null);

  // Sub-graph pane tracking: maps seriesKey → pane index (1-based; 0 = main chart)
  const subgraphPaneAssignmentsRef = useRef<Map<string, number>>(new Map());
  const nextSubgraphPaneRef = useRef<number>(1);
  const initializedSubgraphPanesRef = useRef<Set<number>>(new Set());
  // Tracks the pane each line series was actually created on so we can detect changes
  const lineSeriesActualPaneRef = useRef<Map<string, number>>(new Map());
  const areaSeriesActualPaneRef = useRef<Map<string, number>>(new Map());
  const overlayRenderLatencyMsRef = useRef<number[]>([]);
  const overlayRenderLastLogAtRef = useRef<number>(0);

  const updateVisibleLogicalRange = useCallback((range: { from: number; to: number } | null) => {
    setVisibleLogicalRange(normalizeLogicalRange(range));
  }, []);

  const moveViewportToStart = useCallback((nextStart: number) => {
    const chart = chartRef.current;
    const logicalRange = visibleLogicalRange;

    if (!chart || !logicalRange || historyBarCount <= 0) {
      return;
    }

    const visibleSpan = Math.max(1, logicalRange.to - logicalRange.from);
    const maxStart = Math.max(0, historyBarCount - visibleSpan);
    const clampedStart = clamp(nextStart, 0, maxStart);

    chart.timeScale().setVisibleLogicalRange({
      from: clampedStart,
      to: clampedStart + visibleSpan,
    });
  }, [historyBarCount, visibleLogicalRange]);

  const navigatorMetrics = useMemo(() => {
    const logicalRange = visibleLogicalRange;
    if (!logicalRange || historyBarCount <= 0) {
      return null;
    }

    const visibleSpan = Math.max(1, logicalRange.to - logicalRange.from);
    const maxStart = Math.max(0, historyBarCount - visibleSpan);
    const effectiveStart = clamp(logicalRange.from, 0, maxStart);
    const sliderValue = maxStart === 0 ? 1000 : Math.round((effectiveStart / maxStart) * 1000);
    const visibleBars = Math.max(1, Math.round(visibleSpan));
    const leftBars = Math.max(0, Math.round(effectiveStart));
    const rightBars = Math.max(0, Math.round(maxStart - effectiveStart));

    return {
      sliderValue,
      visibleBars,
      maxStart,
    };
  }, [historyBarCount, visibleLogicalRange]);

  const handleNavigatorChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const metrics = navigatorMetrics;
    if (!metrics) {
      return;
    }

    const rawValue = Number(event.target.value);
    if (!Number.isFinite(rawValue)) {
      return;
    }

    const ratio = clamp(rawValue / 1000, 0, 1);
    moveViewportToStart(metrics.maxStart * ratio);
  }, [moveViewportToStart, navigatorMetrics]);

  useEffect(() => {
    if (!isScrubberActive) {
      return;
    }

    const handlePointerEnd = () => setIsScrubberActive(false);
    window.addEventListener("pointerup", handlePointerEnd);
    window.addEventListener("pointercancel", handlePointerEnd);

    return () => {
      window.removeEventListener("pointerup", handlePointerEnd);
      window.removeEventListener("pointercancel", handlePointerEnd);
    };
  }, [isScrubberActive]);

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
      setHistoryBarCount(chartData.length);
      
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent();
        updateVisibleLogicalRange(chartRef.current.timeScale().getVisibleLogicalRange());
      }

      setCandleWindowRevision((current) => current + 1);
      
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
    const compatChart = chart as ChartCompatApi;
    let series: ISeriesApi<any>;
    
    if (candleType === "bar") {
      series = addBarSeriesCompat(compatChart, {
        upColor: chartUp,
        downColor: chartDown,
        priceLineVisible: false,
      });
    } else if (candleType === "line") {
      series = addLineSeriesCompat(compatChart, {
        color: chartUp,
        lineWidth: 2,
        priceLineVisible: false,
      });
    } else if (candleType === "area") {
      series = addAreaSeriesCompat(compatChart, {
        topColor: `${chartUp}40`,
        bottomColor: `${chartUp}05`,
        lineColor: chartUp,
        lineWidth: 2,
        priceLineVisible: false,
      });
    } else {
      // Default to candlestick
      series = addCandlestickSeriesCompat(compatChart, {
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
      setHistoryBarCount(candleDataRef.current.size);
      
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

        const handleVisibleLogicalRangeChange = (range: { from: number; to: number } | null) => {
          updateVisibleLogicalRange(range);
        };

        chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleLogicalRangeChange);
        updateVisibleLogicalRange(chart.timeScale().getVisibleLogicalRange());

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
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleLogicalRangeChange);
        chart.unsubscribeClick(handleChartClick);
        chart.remove();
      }
      // Null out refs so history loading knows the chart was destroyed
      chartRef.current = null;
      seriesRef.current = null;
      setVisibleLogicalRange(null);
      setHistoryBarCount(0);
      // Reset sub-graph pane tracking so fresh pane indices are assigned after chart recreation
      subgraphPaneAssignmentsRef.current.clear();
      nextSubgraphPaneRef.current = 1;
      initializedSubgraphPanesRef.current.clear();
      lineSeriesActualPaneRef.current.clear();
      areaSeriesActualPaneRef.current.clear();
      areaSeriesMarkersRef.current.clear();
      seriesMarkersRef.current = null;
      seriesMarkersSeriesRef.current = null;
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

    const renderStartedAt = performance.now();

    try {
      const chart = chartRef.current;
      const existingLineSeries = overlayLineSeriesRef.current;
      const existingAreaSeries = overlayAreaSeriesRef.current;
      const incomingAgentIds = new Set(overlayData.keys());
      const candleTimes = Array.from(candleDataRef.current.keys());
      const hasCandleWindow = candleTimes.length > 0;
      const minCandleTime = hasCandleWindow ? findMin(candleTimes) : null;
      const maxCandleTime = hasCandleWindow ? findMax(candleTimes) : null;
      const isInCandleWindow = (time: number): boolean => {
        if (!hasCandleWindow || minCandleTime === null || maxCandleTime === null) {
          return true;
        }
        return time >= minCandleTime && time <= maxCandleTime;
      };

      for (const [seriesKey, overlaySeries] of existingLineSeries.entries()) {
        if (!incomingAgentIds.has(seriesKey)) {
          chart.removeSeries(overlaySeries);
          existingLineSeries.delete(seriesKey);
          subgraphPaneAssignmentsRef.current.delete(seriesKey);
          lineSeriesActualPaneRef.current.delete(seriesKey);
        }
      }

      for (const [seriesKey, areaSeries] of existingAreaSeries.entries()) {
        if (!incomingAgentIds.has(seriesKey)) {
          const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
          if (areaMarkersApi) {
            areaMarkersApi.setMarkers([] as SeriesMarker<Time>[]);
            areaSeriesMarkersRef.current.delete(seriesKey);
          }
          chart.removeSeries(areaSeries.upperSeries);
          chart.removeSeries(areaSeries.lowerSeries);
          existingAreaSeries.delete(seriesKey);
          subgraphPaneAssignmentsRef.current.delete(seriesKey);
          areaSeriesActualPaneRef.current.delete(seriesKey);
        }
      }

      // Helper: get a stable sub-pane index for a series key, allocating a new one if needed
      const getSubgraphPane = (key: string): number => {
        if (subgraphPaneAssignmentsRef.current.has(key)) {
          return subgraphPaneAssignmentsRef.current.get(key)!;
        }
        const pane = nextSubgraphPaneRef.current;
        nextSubgraphPaneRef.current += 1;
        subgraphPaneAssignmentsRef.current.set(key, pane);
        return pane;
      };

      const ensureSubgraphPaneStretch = (paneIndex: number) => {
        if (paneIndex === 0 || initializedSubgraphPanesRef.current.has(paneIndex)) {
          return;
        }
        const pane = chart.panes().find((candidate) => candidate.paneIndex() === paneIndex);
        if (!pane) {
          return;
        }
        pane.setStretchFactor(SUBGRAPH_PANE_STRETCH_FACTOR);
        initializedSubgraphPanesRef.current.add(paneIndex);
      };

      for (const [seriesKey, records] of overlayData.entries()) {
        try {
          const schema = overlaySchemas?.get(seriesKey) || "line";
          const agentId = overlayAgentId(seriesKey);
          const seriesColor = overlayLineColors?.get(agentId) || cssVar("--chart-text", "#cbd5e1");
          const areaStyleOverride = overlayAreaStyles?.get(agentId);
          const upColor = cssVar("--chart-up", "#22c55e");
          const downColor = cssVar("--chart-down", "#ef4444");
          const expectedIntervalSeconds = intervalToSeconds(selectedInterval) || intervalToSeconds(records[0]?.metadata && typeof records[0].metadata === "object"
            ? String((records[0].metadata as Record<string, unknown>).aggregation_interval || "")
            : "") || null;
          const maxAreaGapSeconds = expectedIntervalSeconds ? expectedIntervalSeconds * 1.5 : null;

          // --- Histogram schema: always render in a dedicated sub-graph pane ---
          if (schema === "histogram") {
            const paneKey = resolveSubgraphPaneKey(schema, records, agentId, seriesKey);
            const subPane = getSubgraphPane(paneKey);
            ensureSubgraphPaneStretch(subPane);
            let histSeries = existingLineSeries.get(seriesKey);
            if (!histSeries) {
              histSeries = addHistogramSeriesCompat(chart as ChartCompatApi, {
                color: seriesColor,
                priceLineVisible: false,
                lastValueVisible: true,
                pane: subPane,
              } as any);
              existingLineSeries.set(seriesKey, histSeries);

              const possibleAreaSeries = existingAreaSeries.get(seriesKey);
              if (possibleAreaSeries) {
                const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
                if (areaMarkersApi) {
                  areaMarkersApi.setMarkers([] as SeriesMarker<Time>[]);
                  areaSeriesMarkersRef.current.delete(seriesKey);
                }
                chart.removeSeries(possibleAreaSeries.upperSeries);
                chart.removeSeries(possibleAreaSeries.lowerSeries);
                existingAreaSeries.delete(seriesKey);
              }
            } else {
              histSeries.applyOptions({ color: seriesColor });
            }

            const histData = records
              .map((record) => {
                const time = toUnixSeconds(record.ts);
                const value = Number(record.value);
                if (time === null || !Number.isFinite(value) || !isInCandleWindow(time)) return null;
                const explicitColor = readOverlayColor(record);
                const color = explicitColor || (value >= 0 ? upColor : downColor);
                return { time, value, color };
              })
              .filter((p): p is { time: number; value: number; color: string } => p !== null)
              .sort((a, b) => a.time - b.time);

            const uniqueHistData = new Map<number, { time: number; value: number; color: string }>();
            for (const point of histData) uniqueHistData.set(point.time, point);
            const dedupHistData = Array.from(uniqueHistData.values()).sort((a, b) => a.time - b.time);

            histSeries.setData(dedupHistData.length > 0 ? dedupHistData : []);
            continue;
          }

          // --- Area schema: supports sub-graph routing via metadata/UI override ---
          if (schema === "area") {
            const wantsSubgraph = isSubgraphOverlay(schema, records, agentId, overlaySubgraphForced);
            const paneKey = resolveSubgraphPaneKey(schema, records, agentId, seriesKey);
            const areaPane = wantsSubgraph ? getSubgraphPane(paneKey) : 0;
            const shouldShowAreaLabels = areaStyleOverride?.showLabels !== false;
            if (wantsSubgraph) {
              ensureSubgraphPaneStretch(areaPane);
            }
            let areaSeries = existingAreaSeries.get(seriesKey);

            const actualPane = areaSeriesActualPaneRef.current.get(seriesKey) ?? 0;
            if (areaSeries && actualPane !== areaPane) {
              const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
              if (areaMarkersApi) {
                areaMarkersApi.setMarkers([] as SeriesMarker<Time>[]);
                areaSeriesMarkersRef.current.delete(seriesKey);
              }
              chart.removeSeries(areaSeries.upperSeries);
              chart.removeSeries(areaSeries.lowerSeries);
              existingAreaSeries.delete(seriesKey);
              areaSeriesActualPaneRef.current.delete(seriesKey);
              if (!wantsSubgraph) subgraphPaneAssignmentsRef.current.delete(seriesKey);
              areaSeries = undefined;
            }

            if (!areaSeries) {
              const upperSeries = addLineSeriesCompat(chart as ChartCompatApi, {
                color: "rgba(0, 0, 0, 0)",
                lineWidth: 0,
                lineVisible: false,
                crosshairMarkerVisible: false,
                priceLineVisible: false,
                lastValueVisible: false,
                pane: areaPane,
              });
              const lowerSeries = addLineSeriesCompat(chart as ChartCompatApi, {
                color: "rgba(0, 0, 0, 0)",
                lineWidth: 0,
                lineVisible: false,
                crosshairMarkerVisible: false,
                priceLineVisible: false,
                lastValueVisible: false,
                pane: areaPane,
              });

              const defaultStyle = readAreaRenderStyle(
                undefined,
                seriesColor,
                areaStyleOverride,
                upColor,
                downColor,
              );
              const primitive = new AreaBetweenPrimitive(chart, upperSeries, defaultStyle);
              upperSeries.attachPrimitive(primitive);

              areaSeries = { upperSeries, lowerSeries, primitive };
              existingAreaSeries.set(seriesKey, areaSeries);
              areaSeriesActualPaneRef.current.set(seriesKey, areaPane);

              const possibleLineSeries = existingLineSeries.get(seriesKey);
              if (possibleLineSeries) {
                chart.removeSeries(possibleLineSeries);
                existingLineSeries.delete(seriesKey);
                lineSeriesActualPaneRef.current.delete(seriesKey);
              }
            }

            const areaPoints = records
              .map((record) => {
                const time = toUnixSeconds(record.ts);
                const upper = Number((record as AreaRecord).upper);
                const lower = Number((record as AreaRecord).lower);

                if (
                  time === null ||
                  !Number.isFinite(upper) ||
                  !Number.isFinite(lower) ||
                  !isInCandleWindow(time)
                ) {
                  return null;
                }

                const style = readAreaRenderStyle(
                  record,
                  seriesColor,
                  areaStyleOverride,
                  upColor,
                  downColor,
                );

                return { time, upper, lower, style };
              })
              .filter((point): point is AreaPoint => point !== null)
              .sort((a, b) => a.time - b.time);

            const uniqueAreaDataByTime = new Map<number, AreaPoint>();
            for (const point of areaPoints) {
              uniqueAreaDataByTime.set(point.time, point);
            }

            const deduplicatedAreaData = Array.from(uniqueAreaDataByTime.values()).sort(
              (a, b) => a.time - b.time
            );

            if (deduplicatedAreaData.length > 0) {
              const fallbackStyle = readAreaRenderStyle(
                records[records.length - 1],
                seriesColor,
                areaStyleOverride,
                upColor,
                downColor,
              );

              areaSeries.upperSeries.applyOptions({
                color: "rgba(0, 0, 0, 0)",
                lineWidth: 0,
                lineVisible: false,
                crosshairMarkerVisible: false,
              });
              areaSeries.lowerSeries.applyOptions({
                color: "rgba(0, 0, 0, 0)",
                lineWidth: 0,
                lineVisible: false,
                crosshairMarkerVisible: false,
              });

              areaSeries.upperSeries.setData(
                deduplicatedAreaData.map((point) => ({ time: point.time, value: point.upper }))
              );
              areaSeries.lowerSeries.setData(
                deduplicatedAreaData.map((point) => ({ time: point.time, value: point.lower }))
              );
              areaSeries.primitive.update(deduplicatedAreaData, fallbackStyle, maxAreaGapSeconds);

              const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
              const setAreaMarkers = (markers: SeriesMarker<Time>[]) => {
                if (!areaMarkersApi) {
                  const nextApi = createSeriesMarkers(areaSeries.upperSeries as any, markers);
                  areaSeriesMarkersRef.current.set(seriesKey, nextApi);
                } else {
                  areaMarkersApi.setMarkers(markers);
                }
              };

              if (!shouldShowAreaLabels) {
                setAreaMarkers([] as SeriesMarker<Time>[]);
              } else {
                let latestLabeledRecord: OverlayRecord | null = null;
                for (let index = records.length - 1; index >= 0; index -= 1) {
                  const candidate = records[index];
                  const candidateLabel = readAreaLabel(candidate);
                  const candidateTime = toUnixSeconds(candidate.ts);
                  if (!candidateLabel || candidateTime === null || !isInCandleWindow(candidateTime)) {
                    continue;
                  }
                  latestLabeledRecord = candidate;
                  break;
                }

                if (!latestLabeledRecord) {
                  setAreaMarkers([] as SeriesMarker<Time>[]);
                } else {
                  const labelText = readAreaLabel(latestLabeledRecord);
                  const labelTime = toUnixSeconds(latestLabeledRecord.ts);
                  if (!labelText || labelTime === null) {
                    setAreaMarkers([] as SeriesMarker<Time>[]);
                  } else {
                    const markerText = labelText.length > 32 ? `${labelText.slice(0, 29)}...` : labelText;
                    setAreaMarkers([
                      {
                        time: labelTime,
                        position: "aboveBar",
                        shape: "square",
                        color: fallbackStyle.primaryColor || seriesColor,
                        text: markerText,
                      } as SeriesMarker<Time>,
                    ]);
                  }
                }
              }
            } else {
              areaSeries.upperSeries.setData([]);
              areaSeries.lowerSeries.setData([]);
              areaSeries.primitive.update(
                [],
                readAreaRenderStyle(
                  undefined,
                  seriesColor,
                  areaStyleOverride,
                  upColor,
                  downColor,
                ),
                maxAreaGapSeconds,
              );
              const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
              if (areaMarkersApi) {
                areaMarkersApi.setMarkers([] as SeriesMarker<Time>[]);
              }
            }

            continue;
          }

          // --- Line / band / forecast / event schemas ---
          // Sub-graph detection: UI-level override OR agent metadata opt-in
          const wantsSubgraph = isSubgraphOverlay(schema, records, agentId, overlaySubgraphForced);
          const paneKey = resolveSubgraphPaneKey(schema, records, agentId, seriesKey);
          const linePane = wantsSubgraph ? getSubgraphPane(paneKey) : 0;
          if (wantsSubgraph) {
            ensureSubgraphPaneStretch(linePane);
          }
          const explicitLineColor = readOverlayColor(records[records.length - 1]);
          const trendMode = hasTrendColorMode(records);

          let overlaySeries = existingLineSeries.get(seriesKey);

          // If the desired pane changed (e.g. force_subgraph toggled), the series must be
          // destroyed and recreated — lightweight-charts does not support moving series between panes.
          const actualPane = lineSeriesActualPaneRef.current.get(seriesKey) ?? 0;
          if (overlaySeries && actualPane !== linePane) {
            chart.removeSeries(overlaySeries);
            existingLineSeries.delete(seriesKey);
            lineSeriesActualPaneRef.current.delete(seriesKey);
            if (!wantsSubgraph) subgraphPaneAssignmentsRef.current.delete(seriesKey);
            overlaySeries = undefined;
          }

          if (!overlaySeries) {
            let resolvedLineColor = explicitLineColor || seriesColor;
            if (!explicitLineColor && trendMode && records.length > 1) {
              const firstValue = Number(records[0]?.value);
              const lastValue = Number(records[records.length - 1]?.value);
              if (Number.isFinite(firstValue) && Number.isFinite(lastValue)) {
                resolvedLineColor = lastValue >= firstValue ? upColor : downColor;
              }
            }
            overlaySeries = addLineSeriesCompat(chart as ChartCompatApi, {
              color: resolvedLineColor,
              lineWidth: 2,
              priceLineVisible: false,
              lastValueVisible: wantsSubgraph, // show last value label on sub-pane
              pane: linePane,
            } as any);
            existingLineSeries.set(seriesKey, overlaySeries);
            lineSeriesActualPaneRef.current.set(seriesKey, linePane);

            const possibleAreaSeries = existingAreaSeries.get(seriesKey);
            if (possibleAreaSeries) {
              const areaMarkersApi = areaSeriesMarkersRef.current.get(seriesKey);
              if (areaMarkersApi) {
                areaMarkersApi.setMarkers([] as SeriesMarker<Time>[]);
                areaSeriesMarkersRef.current.delete(seriesKey);
              }
              chart.removeSeries(possibleAreaSeries.upperSeries);
              chart.removeSeries(possibleAreaSeries.lowerSeries);
              existingAreaSeries.delete(seriesKey);
              areaSeriesActualPaneRef.current.delete(seriesKey);
            }
          } else {
            let resolvedLineColor = explicitLineColor || seriesColor;
            if (!explicitLineColor && trendMode && records.length > 1) {
              const firstValue = Number(records[0]?.value);
              const lastValue = Number(records[records.length - 1]?.value);
              if (Number.isFinite(firstValue) && Number.isFinite(lastValue)) {
                resolvedLineColor = lastValue >= firstValue ? upColor : downColor;
              }
            }
            overlaySeries.applyOptions({ color: resolvedLineColor });
          }

          const lineData = records
            .map((record) => {
              const time = toUnixSeconds(record.ts);
              const value = Number(record.value);
              if (time === null || !Number.isFinite(value) || !isInCandleWindow(time)) {
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
          } else {
            overlaySeries.setData([]);
          }
        } catch (error) {
          console.error(`[ChartView] Failed to render overlay for series ${seriesKey}:`, error);
        }
      }

    } catch (error) {
      console.error("[ChartView] Failed to update overlay series:", error);
    } finally {
      const elapsedMs = performance.now() - renderStartedAt;
      const samples = overlayRenderLatencyMsRef.current;
      samples.push(elapsedMs);
      if (samples.length > 240) {
        samples.shift();
      }

      const now = Date.now();
      if (now - overlayRenderLastLogAtRef.current >= 5000 && samples.length >= 10) {
        const ordered = [...samples].sort((a, b) => a - b);
        const p50 = ordered[Math.floor(ordered.length * 0.5)] ?? 0;
        const p95 = ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * 0.95))] ?? 0;
        const max = ordered[ordered.length - 1] ?? 0;
        const avg = ordered.reduce((sum, value) => sum + value, 0) / ordered.length;

        console.debug("[ChartView][Perf] overlay render latency", {
          sample_count: ordered.length,
          avg_ms: Number(avg.toFixed(2)),
          p50_ms: Number(p50.toFixed(2)),
          p95_ms: Number(p95.toFixed(2)),
          max_ms: Number(max.toFixed(2)),
        });

        overlayRenderLastLogAtRef.current = now;
      }
    }
  }, [overlayData, overlaySchemas, overlayLineColors, overlayAreaStyles, overlaySubgraphForced, candleWindowRevision, SUBGRAPH_PANE_STRETCH_FACTOR]);

  useEffect(() => {
    if (!seriesRef.current) {
      return;
    }

    const candleTimes = Array.from(candleDataRef.current.keys());
    const hasCandleWindow = candleTimes.length > 0;
    const minCandleTime = hasCandleWindow ? findMin(candleTimes) : null;
    const maxCandleTime = hasCandleWindow ? findMax(candleTimes) : null;

    const markerByTime = new Map<number, {
      time: number;
      position: "aboveBar" | "belowBar";
      color: string;
      shape: "arrowUp" | "arrowDown";
      text: string;
    }>();
    const markerUpColor = cssVar("--chart-up", "#22c55e");
    const markerDownColor = cssVar("--chart-down", "#ef4444");

    for (const marker of tradeMarkers) {
      const time = toUnixSeconds(marker.ts);
      if (time === null) {
        continue;
      }
      if (
        hasCandleWindow &&
        minCandleTime !== null &&
        maxCandleTime !== null &&
        (time < minCandleTime || time > maxCandleTime)
      ) {
        continue;
      }

      const action = (marker.action || "").toUpperCase();
      const title = (marker.title || "").toUpperCase();
      const isEntry = action.endsWith("ENTRY") || title.includes("ENTRY");
      const isExit = action.endsWith("EXIT") || title.includes("EXIT");
      const isLong = action.includes("LONG") || title.includes("LONG");
      const isShort = action.includes("SHORT") || title.includes("SHORT");

      if (!isEntry && !isExit) {
        continue;
      }

      // Build label: LE=LONG ENTRY, LX=LONG EXIT, SE=SHORT ENTRY, SX=SHORT EXIT
      let markerLabel = "?";
      if (isLong && isEntry) markerLabel = "LE";
      else if (isLong && isExit) markerLabel = "LX";
      else if (isShort && isEntry) markerLabel = "SE";
      else if (isShort && isExit) markerLabel = "SX";
      else if (isEntry) markerLabel = "E"; // Fallback
      else if (isExit) markerLabel = "X"; // Fallback

      markerByTime.set(time, {
        time,
        position: isEntry ? "belowBar" : "aboveBar",
        color: isEntry ? markerUpColor : markerDownColor,
        shape: isEntry ? "arrowUp" : "arrowDown",
        text: markerLabel,
      });
    }

    const markerData = Array.from(markerByTime.values()).sort((a, b) => a.time - b.time);
    const currentSeries = seriesRef.current;
    const legacySetMarkers = (currentSeries as any)?.setMarkers;
    if (typeof legacySetMarkers === "function") {
      legacySetMarkers.call(currentSeries, markerData);
      return;
    }

    const typedMarkers = markerData as SeriesMarker<Time>[];
    if (!seriesMarkersRef.current || seriesMarkersSeriesRef.current !== currentSeries) {
      seriesMarkersRef.current = createSeriesMarkers(currentSeries as any, typedMarkers);
      seriesMarkersSeriesRef.current = currentSeries as any;
    } else {
      seriesMarkersRef.current.setMarkers(typedMarkers);
    }
  }, [tradeMarkers, candleWindowRevision]);

  const timeframeOptions = [
    { label: "1D", days: 1 },
    { label: "1W", days: 7 },
    { label: "1M", days: 30 },
    { label: "3M", days: 90 },
    { label: "6M", days: 180 },
    { label: "1Y", days: 365 },
  ];

  return (
    <div className="chart-view-shell">
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

              <div style={{ height: "1px", background: "#333", margin: "6px 0" }} />

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ color: "#999", fontSize: "13px" }}>Indicator Values:</span>
                <span style={{ color: "#64748b", fontSize: "11px" }}>
                  {selectedIndicatorRows.length > 0 ? `${selectedIndicatorRows.length} found` : "none at this candle"}
                </span>
              </div>

              {selectedIndicatorRows.length > 0 && (
                <div
                  style={{
                    maxHeight: "180px",
                    overflowY: "auto",
                    border: "1px solid #333",
                    borderRadius: "4px",
                    padding: "8px",
                    background: "rgba(15, 23, 42, 0.45)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                  }}
                >
                  {selectedIndicatorRows.map((row, index) => (
                    <div
                      key={`${row.seriesName}:${row.field}:${index}`}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: "12px",
                        fontFamily: "monospace",
                        gap: "12px",
                      }}
                    >
                      <span style={{ color: "#94a3b8" }}>{row.seriesName}:{row.field}</span>
                      <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{row.value.toFixed(6)}</span>
                    </div>
                  ))}
                </div>
              )}
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

      <div ref={containerRef} className="chart-canvas" />

      {navigatorMetrics && historyBarCount > navigatorMetrics.visibleBars + 3 && (
        <div className="chart-scrubber-dock" role="group" aria-label="Timeline scrubber">
          <input
            id="chart-timeline-scrubber"
            type="range"
            min="0"
            max="1000"
            step="1"
            value={navigatorMetrics.sliderValue}
            onChange={handleNavigatorChange}
            onPointerDown={() => setIsScrubberActive(true)}
            className={`chart-nav-slider ${isScrubberActive ? "is-active" : ""}`}
            aria-label="Scrub chart timeline"
          />
        </div>
      )}
    </div>
  );
}
