/** Shared types for the Operator TUI. */

export type ViewName =
  | "mail"
  | "agents"
  | "events"
  | "traces"
  | "config"
  | "metrics"
  | "settings";

export const VIEWS: readonly ViewName[] = [
  "mail",
  "agents",
  "events",
  "traces",
  "config",
  "metrics",
  "settings",
];

export const VIEW_TITLES: Record<ViewName, string> = {
  mail: "Mail",
  agents: "Agents",
  events: "Events",
  traces: "Traces",
  config: "Config",
  metrics: "Metrics",
  settings: "Settings",
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

/** A mail as serialized by the server's `Mail.to_dict()`. */
export interface MailItem {
  id: string;
  from_address: string;
  to: string[];
  cc: string[];
  subject: string | null;
  content: string;
  in_reply_to: string | null;
  thread_id: string;
  forward_of: string | null;
  timestamp: string;
}

/** A conversation thread: mails sharing one `thread_id`. */
export interface ThreadGroup {
  threadId: string;
  /** Mails in chronological order (oldest first). */
  mails: MailItem[];
  /** Local parts of every participant address except my own (deduplicated). */
  participants: string[];
  /** Latest mail in the thread (drives list ordering and previews). */
  latest: MailItem;
  count: number;
}

/** A participant entry from `GET /participants`. */
export interface ParticipantInfo {
  id: string;
  name?: string;
  type?: string;
  metadata?: Record<string, unknown>;
}
