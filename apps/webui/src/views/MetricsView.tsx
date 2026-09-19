import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import { groupMetrics, summarizeLlmUsage } from "../lib/snapshot";
import type { LlmUsageRow, MetricsSnapshot } from "../lib/snapshot";
import type { ViewProps } from "../types";
import "../styles/metrics.css";

/** Polling interval; same convention as the TUI metrics view. */
const POLL_INTERVAL_MS = 2_000;

function formatHitRate(rate: number | null): string {
  return rate === null ? "-" : `${(rate * 100).toFixed(1)}%`;
}

/** One LLM USAGE line: identity (agent/model or TOTAL) + the five counters. */
function LlmUsageLine({ row, total }: { row: LlmUsageRow; total?: boolean }): ReactElement {
  return (
    <div className={total ? "llm-usage-row llm-usage-total" : "llm-usage-row"}>
      <span className="llm-usage-id">
        {total ? "TOTAL" : `${row.agentId} · ${row.model}`}
      </span>
      <span className="llm-usage-stat">
        PROMPT <b>{row.promptTokens.toLocaleString()}</b>
      </span>
      <span className="llm-usage-stat">
        COMPLETION <b>{row.completionTokens.toLocaleString()}</b>
      </span>
      <span className="llm-usage-stat">
        CACHED <b>{row.cachedTokens.toLocaleString()}</b>
      </span>
      <span className="llm-usage-stat">
        CACHE WRITE <b>{row.cacheWriteTokens.toLocaleString()}</b>
      </span>
      <span className="llm-usage-stat llm-usage-hit-rate">
        HIT RATE <b>{formatHitRate(row.cacheHitRate)}</b>
      </span>
    </div>
  );
}

/**
 * METRICS — dashboard of the server's in-process metrics (GET /metrics),
 * grouped by mail/agent/llm/tool/other via lib/snapshot.ts, polled every 2s.
 * Counter/gauge rows show a large glowing value; histogram rows show
 * count/avg/min/max (formatted by lib/snapshot.ts). Each row also shows the
 * metric description and unit as a dim subtitle when present.
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
  const llmUsage = summarizeLlmUsage(snapshot ?? {});

  return (
    <section className="metrics-view">
      <div className="metrics-toolbar">
        <h2 className="tech-label metrics-title">METRICS</h2>
        <span className="tech-label">AUTO {POLL_INTERVAL_MS / 1000}S</span>
        {error ? <span className="tech-label metrics-error">{error}</span> : null}
      </div>
      {llmUsage.rows.length > 0 ? (
        <div className="panel llm-usage-panel">
          <h3 className="tech-label metric-group-title">LLM USAGE</h3>
          <LlmUsageLine row={llmUsage.total} total />
          {llmUsage.rows.map((row) => (
            <LlmUsageLine row={row} key={`${row.agentId}:${row.model}`} />
          ))}
        </div>
      ) : null}
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
                  {row.description || row.unit ? (
                    <div className="metric-desc">
                      {row.description}
                      {row.unit ? ` (${row.unit})` : ""}
                    </div>
                  ) : null}
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
