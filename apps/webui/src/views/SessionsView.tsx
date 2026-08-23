import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import type { SessionInfo } from "../client/rest";
import { formatConnectedAt, formatFilters, sortSessions } from "../lib/sessions";
import type { ViewProps } from "../types";
import "../styles/sessions.css";

/** Polling interval; sessions change rarely, so slower than metrics. */
const POLL_INTERVAL_MS = 5_000;

/**
 * SESSIONS — active client sessions on the server (GET /sessions),
 * polled every 5s. One row per session: id, client type, subscription
 * mode, connection time and event filters. The webui itself appears
 * here as a "webui" session. The table auto-refreshes; REFRESH forces
 * an immediate re-fetch.
 */
export function SessionsView({ rest }: ViewProps): ReactElement {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [count, setCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await rest.listSessions();
      setSessions(sortSessions(data.sessions));
      setCount(data.count);
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

  return (
    <section className="panel view-panel sessions-view">
      <div className="sessions-toolbar">
        <h2 className="view-title tech-label">SESSIONS</h2>
        <span className="tech-label">{count} ACTIVE</span>
        <span className="tech-label">AUTO {POLL_INTERVAL_MS / 1000}S</span>
        {error ? <span className="tech-label sessions-error">{error}</span> : null}
        <span className="sessions-spacer" />
        <button type="button" className="btn btn--secondary" onClick={() => void load()}>
          REFRESH
        </button>
      </div>
      <table className="sessions-table">
        <thead>
          <tr>
            <th className="tech-label">ID</th>
            <th className="tech-label">CLIENT</th>
            <th className="tech-label">MODE</th>
            <th className="tech-label">CONNECTED</th>
            <th className="tech-label">FILTERS</th>
          </tr>
        </thead>
        <tbody>
          {sessions.length === 0 ? (
            <tr>
              <td className="sessions-empty" colSpan={5}>
                no active sessions
              </td>
            </tr>
          ) : (
            sessions.map((session) => (
              <tr key={session.id}>
                <td className="session-id" title={session.id}>
                  {session.id}
                </td>
                <td className="session-client-type">{session.client_type}</td>
                <td className="session-mode">{session.mode}</td>
                <td className="session-connected">{formatConnectedAt(session.connected_at)}</td>
                <td className="session-filters">{formatFilters(session.filters)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
}
