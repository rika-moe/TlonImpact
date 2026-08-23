import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ReactElement } from "react";
import type { LogEntry } from "../client/rest";
import { formatTimestamp, levelClass, logJson, logMessage, strField } from "../lib/logs";
import type { ViewProps } from "../types";
import "../styles/logs.css";

const DEFAULT_LIMIT = 200;
const AUTO_REFRESH_MS = 5_000;

/** Minimum-level filter; "" means no level filter. */
const LEVEL_OPTIONS = ["", "debug", "info", "warning", "error"] as const;

interface LogFilters {
  level: string;
  agentId: string;
  traceId: string;
}

/**
 * LOGS — server log stream (GET /logs), a webui-only view with no TUI
 * counterpart. Filters: minimum level (dropdown, applies instantly),
 * agent_id and trace_id (applied on REFRESH / next auto-refresh tick);
 * all filters combine server-side. The latest entries render in a
 * terminal panel, newest at the bottom; clicking a row expands the
 * entry's full JSON (same interaction as the Events view).
 * AUTO re-fetches every 5s.
 */
export function LogsView({ rest }: ViewProps): ReactElement {
  const [filters, setFilters] = useState<LogFilters>({ level: "", agentId: "", traceId: "" });
  // Ref mirror so `load` (stable identity) always sees current filters —
  // typing in a filter input must not trigger a fetch per keystroke. All
  // updates go through `applyFilters`, which writes the ref synchronously.
  const filtersRef = useRef(filters);

  const applyFilters = (patch: Partial<LogFilters>): LogFilters => {
    const next = { ...filtersRef.current, ...patch };
    filtersRef.current = next;
    setFilters(next);
    return next;
  };
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [count, setCount] = useState(0);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<LogEntry | null>(null);
  const termRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const current = filtersRef.current;
    try {
      const data = await rest.getLogs({
        level: current.level || undefined,
        agent_id: current.agentId.trim() || undefined,
        trace_id: current.traceId.trim() || undefined,
        limit: DEFAULT_LIMIT,
      });
      setLogs(data.logs);
      setCount(data.count);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!auto) return;
    const timer = setInterval(() => void load(), AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [auto, load]);

  // The log list is chronological; stick to the newest entries.
  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const onLevelChange = (e: ChangeEvent<HTMLSelectElement>): void => {
    applyFilters({ level: e.target.value });
    // The dropdown is a discrete choice: apply it immediately.
    void load();
  };

  return (
    <section className="panel view-panel logs-view">
      <div className="logs-toolbar">
        <h2 className="view-title tech-label">LOGS</h2>
        <select
          className="input logs-level-select"
          value={filters.level}
          onChange={onLevelChange}
          title="minimum level"
        >
          {LEVEL_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option === "" ? "ALL LEVELS" : option.toUpperCase()}
            </option>
          ))}
        </select>
        <input
          className="input logs-filter-input"
          placeholder="agent_id"
          value={filters.agentId}
          onChange={(e) => applyFilters({ agentId: e.target.value })}
        />
        <input
          className="input logs-filter-input"
          placeholder="trace_id"
          value={filters.traceId}
          onChange={(e) => applyFilters({ traceId: e.target.value })}
        />
        <span className="tech-label">{count} ENTRIES</span>
        {error ? <span className="tech-label logs-error">{error}</span> : null}
        <span className="logs-spacer" />
        <button
          type="button"
          className={`btn ${auto ? "btn--primary" : "btn--secondary"}`}
          onClick={() => setAuto((prev) => !prev)}
        >
          AUTO {AUTO_REFRESH_MS / 1000}S
        </button>
        <button type="button" className="btn btn--secondary" onClick={() => void load()}>
          REFRESH
        </button>
      </div>
      <div className="terminal logs-terminal" ref={termRef}>
        {logs.length === 0 ? (
          <div className="terminal-line logs-empty">no log entries</div>
        ) : null}
        {logs.map((entry, index) => {
          const entryLevel = (strField(entry, "level") ?? "info").toLowerCase();
          const entryTrace = strField(entry, "trace_id");
          const entryAgent = strField(entry, "agent_id");
          return (
            <div key={index}>
              <div
                className={`terminal-line log-row${expanded === entry ? " log-row--expanded" : ""}`}
                onClick={() => setExpanded(expanded === entry ? null : entry)}
              >
                <span className="log-dim">{formatTimestamp(strField(entry, "timestamp"))} </span>
                <span className={levelClass(entryLevel)}>
                  {entryLevel.toUpperCase().padEnd(7)}
                </span>{" "}
                <span className="log-msg">{logMessage(entry)}</span>
                {entryAgent ? <span className="log-dim"> {entryAgent}</span> : null}
                {entryTrace ? (
                  <span className="log-dim"> trace:{entryTrace.slice(0, 8)}</span>
                ) : null}
              </div>
              {expanded === entry ? <pre className="log-json">{logJson(entry)}</pre> : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
