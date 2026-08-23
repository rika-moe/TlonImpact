/**
 * Session list formatting for the Sessions view (pure functions,
 * unit-tested). Rows come from `GET /sessions`.
 */

import type { SessionInfo } from "../client/rest";

const pad2 = (n: number): string => String(n).padStart(2, "0");

/**
 * Render a session's connected_at ISO timestamp. Sessions connected
 * today show only HH:MM:SS; older ones get an MM-DD prefix. An
 * unparseable timestamp is echoed back as-is.
 */
export function formatConnectedAt(ts: string, now: Date = new Date()): string {
  if (!ts) return "??:??:??";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return time;
  return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${time}`;
}

/**
 * Render a session's event filters: "—" when empty, otherwise compact
 * JSON (e.g. {"event_types":["mail.delivered"]}).
 */
export function formatFilters(filters: Record<string, unknown>): string {
  return Object.keys(filters).length === 0 ? "—" : JSON.stringify(filters);
}

/**
 * Newest-connected first, so the session the user just opened (the
 * webui itself) surfaces at the top.
 */
export function sortSessions(sessions: readonly SessionInfo[]): SessionInfo[] {
  return [...sessions].sort((a, b) => (a.connected_at < b.connected_at ? 1 : -1));
}
