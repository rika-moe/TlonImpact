/**
 * Snapshot shapes and grouping helpers for the metrics view (pure
 * functions, unit-tested).
 *
 * Copied from apps/operator/src/metrics/snapshot.ts.
 * `GET /metrics` returns `{"metrics": {name: entry}}` as produced by
 * MetricsService.snapshot() (src/tlon/base/metrics.py); here the flat
 * map is grouped into the four metric families (mail / agent / llm /
 * tool) for display.
 */

/** One data point of a metric (counter/gauge carry `value`, histograms
 * carry `count`/`sum`/`min`/`max`). */
export interface MetricPoint {
  attributes: Record<string, string>;
  value?: number;
  count?: number;
  sum?: number;
  min?: number | null;
  max?: number | null;
}

export interface MetricEntry {
  type: string;
  description: string;
  unit: string;
  points: MetricPoint[];
}

export interface MetricsSnapshot {
  metrics?: Record<string, MetricEntry>;
}

/** One rendered row: metric short name, formatted attributes, value. */
export interface MetricRow {
  metric: string;
  attributes: string;
  value: string;
}

export interface MetricGroup {
  name: string;
  rows: MetricRow[];
}

/** Metric families in display order, matched by name prefix. */
const GROUP_PREFIXES: ReadonlyArray<readonly [string, string]> = [
  ["mail", "tlon.mail."],
  ["agent", "tlon.agent."],
  ["llm", "tlon.llm."],
  ["tool", "tlon.tool."],
];

/** Strip the "tlon.<family>." prefix for compact display. */
function shortName(metric: string): string {
  const parts = metric.split(".");
  return parts.length > 2 ? parts.slice(2).join(".") : metric;
}

/** "agent_id=gu-yanxi" style, keys sorted for stable output. */
export function formatAttributes(attributes: Record<string, string>): string {
  const pairs = Object.keys(attributes)
    .sort()
    .map((key) => `${key}=${attributes[key]}`);
  return pairs.length > 0 ? pairs.join(" ") : "-";
}

/** Counter/gauge: the plain value; histogram: count + avg/min/max. */
export function formatValue(entry: MetricEntry, point: MetricPoint): string {
  if (entry.type === "histogram") {
    const count = point.count ?? 0;
    const sum = point.sum ?? 0;
    const avg = count > 0 ? Math.round(sum / count) : 0;
    const parts = [`count=${count}`, `avg=${avg}${entry.unit}`];
    if (point.min != null) parts.push(`min=${point.min}`);
    if (point.max != null) parts.push(`max=${point.max}`);
    return parts.join(" ");
  }
  return String(point.value ?? 0);
}

/**
 * Group the flat snapshot into the four metric families. Metrics whose
 * name matches no family prefix are dropped; families with no points
 * still appear (empty) so the layout is stable.
 */
export function groupMetrics(snapshot: MetricsSnapshot): MetricGroup[] {
  const metrics = snapshot.metrics ?? {};
  return GROUP_PREFIXES.map(([name, prefix]) => {
    const rows: MetricRow[] = [];
    for (const metricName of Object.keys(metrics).sort()) {
      if (!metricName.startsWith(prefix)) continue;
      const entry = metrics[metricName] as MetricEntry;
      for (const point of entry.points) {
        rows.push({
          metric: shortName(metricName),
          attributes: formatAttributes(point.attributes),
          value: formatValue(entry, point),
        });
      }
    }
    return { name, rows };
  });
}
