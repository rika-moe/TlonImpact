/** Shared types for the Operator TUI. */

export type ViewName = "agents" | "events" | "traces" | "config";

export const VIEWS: readonly ViewName[] = ["agents", "events", "traces", "config"];

export const VIEW_TITLES: Record<ViewName, string> = {
  agents: "Agents",
  events: "Events",
  traces: "Traces",
  config: "Config",
};

export type ConnectionState =
  | "connecting"
  | "authenticating"
  | "connected"
  | "reconnecting"
  | "disconnected";

export interface ConnectionStatus {
  state: ConnectionState;
  sessionId?: string;
  /** Reconnect attempt counter (0 while connected). */
  attempt: number;
  /** Delay before the next reconnect attempt, when reconnecting. */
  nextRetryMs?: number;
  lastError?: string;
}

/** A real-time event pushed by the server (`{"type": "event", ...}`). */
export interface ServerEvent {
  eventType: string;
  payload: unknown;
  source?: string;
  timestamp?: string;
  traceId?: string;
  spanId?: string;
  /** Local receive time, ms since epoch. */
  receivedAt: number;
}

export interface AgentInfo {
  id: string;
  name?: string;
  state?: string;
  [key: string]: unknown;
}

export interface TraceSummary {
  trace_id?: string;
  [key: string]: unknown;
}
