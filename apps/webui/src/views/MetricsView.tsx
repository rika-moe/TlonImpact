import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import { groupMetrics } from "../lib/snapshot";
import type { MetricsSnapshot } from "../lib/snapshot";
import type { ViewProps } from "../types";
import "../styles/metrics.css";

/** Polling interval; same convention as the TUI metrics view. */
const POLL_INTERVAL_MS = 2_000;

/**
 * METRICS — dashboard of the server's in-process metrics (GET /metrics),
 * grouped by mail/agent/llm/tool via lib/snapshot.ts, polled every 2s.
 * Counter/gauge rows show a large glowing value; histogram rows show
 * count/avg/min/max (formatted by lib/snapshot.ts).
 */
export function MetricsView({ rest }: ViewProps): ReactElement {
  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSnapshot(await rest.getMetrics());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const groups = groupMetrics(snapshot ?? {});

  return (
    <section className="metrics-view">
      <div className="metrics-toolbar">
        <h2 className="tech-label metrics-title">METRICS</h2>
        <span className="tech-label">AUTO {POLL_INTERVAL_MS / 1000}S</span>
        {error ? <span className="tech-label metrics-error">{error}</span> : null}
      </div>
      <div className="metrics-grid">
        {groups.map((group) => (
          <div className="panel metric-group" key={group.name}>
            <h3 className="tech-label metric-group-title">{group.name}</h3>
            {group.rows.length === 0 ? (
              <div className="tech-label metric-empty">NO DATA</div>
            ) : (
              group.rows.map((row, index) => (
                <div className="metric-row" key={`${row.metric}:${row.attributes}:${index}`}>
                  <div className="metric-head">
                    <span className="metric-name">{row.metric}</span>
                    <span className="metric-value">{row.value}</span>
                  </div>
                  <div className="metric-attrs">{row.attributes}</div>
                </div>
              ))
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
