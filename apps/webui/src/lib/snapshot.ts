/**
 * Snapshot shapes and grouping helpers for the metrics view (pure
 * functions, unit-tested).
 *
 * Copied from apps/operator/src/metrics/snapshot.ts.
 * `GET /metrics` returns `{"metrics": {name: entry}}` as produced by
 * MetricsService.snapshot() (src/tlon/base/metrics.py); here the flat
 * map is grouped into the four metric families (mail / agent / llm /
 * tool) for display; metrics outside those families fall into "other".
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

/** One rendered row: metric short name, description, unit, formatted
 * attributes, value. */
export interface MetricRow {
  metric: string;
  description: string;
  unit: string;
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
 * Group the flat snapshot into the four metric families, plus an "other"
 * bucket for metrics whose name matches no family prefix. Families with
 * no points still appear (empty) so the layout is stable; "other" only
 * appears when it actually holds rows.
 */
export function groupMetrics(snapshot: MetricsSnapshot): MetricGroup[] {
  const metrics = snapshot.metrics ?? {};
  const buckets = new Map<string, MetricRow[]>();
  for (const [name] of GROUP_PREFIXES) buckets.set(name, []);
  const other: MetricRow[] = [];

  for (const metricName of Object.keys(metrics).sort()) {
    const family = GROUP_PREFIXES.find(([, prefix]) => metricName.startsWith(prefix));
    const entry = metrics[metricName] as MetricEntry;
    const target = family ? (buckets.get(family[0]) as MetricRow[]) : other;
    for (const point of entry.points) {
      target.push({
        metric: family ? shortName(metricName) : metricName,
        description: entry.description,
        unit: entry.unit,
        attributes: formatAttributes(point.attributes),
        value: formatValue(entry, point),
      });
    }
  }

  const result = GROUP_PREFIXES.map(([name]) => ({
    name,
    rows: buckets.get(name) as MetricRow[],
  }));
  if (other.length > 0) result.push({ name: "other", rows: other });
  return result;
}
