/**
 * Shared structlog entry rendering helpers — used by the LOGS view and
 * the AGENTS detail panel's recent-logs section (issue ).
 */

import type { LogEntry } from "../client/rest";

export function strField(entry: LogEntry, key: string): string | undefined {
  const value = entry[key];
  return typeof value === "string" ? value : undefined;
}

export function levelClass(level: string): string {
  switch (level) {
    case "error":
    case "critical":
      return "log-level log-level--error";
    case "warning":
      return "log-level log-level--warning";
    case "debug":
      return "log-level log-level--debug";
    default:
      return "log-level log-level--info";
  }
}

export function formatTimestamp(ts: string | undefined): string {
  if (!ts) return "??:??:??";
  const date = new Date(ts);
  return Number.isNaN(date.getTime())
    ? ts
    : date.toLocaleTimeString("en-GB", { hour12: false });
}

/** The structlog message lives under "event"; fall back to the raw JSON. */
export function logMessage(entry: LogEntry): string {
  return strField(entry, "event") ?? strField(entry, "message") ?? JSON.stringify(entry);
}

/** Full entry JSON for the expanded row: every structlog context field. */
export function logJson(entry: LogEntry): string {
  return JSON.stringify(entry, null, 2);
}
