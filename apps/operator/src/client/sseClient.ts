/**
 * SSE client for the Tlon server (protocol v2, `http://host:port`).
 *
 * Startup flow (see src/tlon/app/server/api/rest.py):
 * - POST /sessions declares username/client_type/mode/filters and returns
 *   the session id plus the participants/agents snapshots (emitted as
 *   "agents"/"participants" events, replacing the WS push frames).
 * - GET /events?session_id=... opens the Server-Sent Events stream. Each
 *   `data:` line carries the same {"type": "event", ...} frame the WS
 *   protocol pushed; ": ping" comment lines are the server heartbeat.
 * - Mode/filter changes go through PATCH /sessions/{id} (`setMode`).
 *
 * On stream loss the client reconnects with exponential backoff and
 * re-establishes the session from scratch (new session id, fresh
 * snapshot) — events during the outage are lost, same as the WS
 * reconnect semantics. `dispose()` aborts the stream and best-effort
 * DELETEs the session (idempotent server-side).
 */

import { EventEmitter } from "node:events";
import type { ConnectionStatus, ServerEvent } from "../types.js";

export const PROTOCOL_VERSION = "2.0.0";
export const BASE_BACKOFF_MS = 1_000;
export const MAX_BACKOFF_MS = 30_000;

/** Exponential backoff delay (deterministic, no jitter — kept pure for tests). */
export function backoffDelay(
  attempt: number,
  baseMs: number = BASE_BACKOFF_MS,
  maxMs: number = MAX_BACKOFF_MS,
): number {
  const exp = baseMs * 2 ** Math.max(0, attempt);
  return Math.min(exp, maxMs);
}

export type SessionMode = "chat" | "event" | "agent";

export interface SseClientOptions {
  host: string;
  port: number;
  clientType?: string;
  filters?: Record<string, unknown>;
  /** Human username for the session (server defaults to "guest" when omitted). */
  username?: string;
}

export class SseClient extends EventEmitter {
  readonly baseUrl: string;
  private sessionId: string | null = null;
  private abort: AbortController | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private attempt = 0;
  private disposed = false;
  private running = false;
  private status: ConnectionStatus = { state: "disconnected", attempt: 0 };

  constructor(private readonly options: SseClientOptions) {
    super();
    this.baseUrl = `http://${options.host}:${options.port}`;
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  getSessionId(): string | null {
    return this.sessionId;
  }

  connect(): void {
    if (this.disposed || this.running) return;
    this.running = true;
    this.setStatus({ state: this.attempt === 0 ? "connecting" : "reconnecting", attempt: this.attempt });
    void this.run();
  }

  /** Switch server-side session mode via PATCH /sessions/{id}. */
  async setMode(mode: SessionMode): Promise<boolean> {
    const sessionId = this.sessionId;
    if (!sessionId) return false;
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
    } catch {
      return false;
    }
    if (!response.ok) return false;
    this.emit("mode_changed", { type: "mode_changed", mode });
    return true;
  }

  dispose(): void {
    this.disposed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.abort?.abort();
    this.abort = null;
    const sessionId = this.sessionId;
    this.sessionId = null;
    if (sessionId) {
      // Best-effort explicit disconnect; the server also cleans up when
      // the aborted stream's heartbeat write fails.
      void fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      }).catch(() => {});
    }
    this.setStatus({ state: "disconnected", attempt: 0 });
  }

  /** One connect attempt: open a session, then stream events until loss. */
  private async run(): Promise<void> {
    try {
      await this.openSession();
      await this.streamEvents();
    } catch (err) {
      if (!this.disposed) {
        this.status = { ...this.status, lastError: err instanceof Error ? err.message : String(err) };
      }
    }
    this.running = false;
    this.sessionId = null;
    this.abort = null;
    if (this.disposed) return;
    this.scheduleReconnect();
  }

  private async openSession(): Promise<void> {
    const response = await fetch(`${this.baseUrl}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: this.options.username,
        client_type: this.options.clientType ?? "tui",
        mode: "chat",
        filters: this.options.filters ?? {},
        protocol_version: PROTOCOL_VERSION,
      }),
    });
    if (!response.ok) {
      throw new Error(`POST /sessions returned ${String(response.status)}`);
    }
    const data = (await response.json()) as Record<string, unknown>;
    if (typeof data["session_id"] !== "string") {
      throw new Error("POST /sessions: response missing session_id");
    }
    this.sessionId = data["session_id"];
    this.attempt = 0;
    this.setStatus({ state: "connected", attempt: 0, sessionId: this.sessionId });
    this.emit("session", data);
    if (Array.isArray(data["agents"])) this.emit("agents", data["agents"]);
    if (Array.isArray(data["participants"])) {
      this.emit("participants", { type: "participants", participants: data["participants"] });
    }
  }

  private async streamEvents(): Promise<void> {
    const sessionId = this.sessionId;
    if (!sessionId) throw new Error("GET /events: no session");
    this.abort = new AbortController();
    const response = await fetch(
      `${this.baseUrl}/events?session_id=${encodeURIComponent(sessionId)}`,
      { headers: { Accept: "text/event-stream" }, signal: this.abort.signal },
    );
    if (!response.ok || response.body === null) {
      throw new Error(`GET /events returned ${String(response.status)}`);
    }

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        this.handleBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
  }

  /** Dispatch one SSE block; heartbeat comments and non-event frames are ignored. */
  private handleBlock(block: string): void {
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) return; // heartbeat comment
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0) return;

    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    } catch {
      return;
    }
    if (msg["type"] !== "event") return;

    const ev: ServerEvent = {
      eventType: String(msg["event_type"] ?? "unknown"),
      payload: msg["payload"],
      source: typeof msg["source"] === "string" ? msg["source"] : undefined,
      timestamp: typeof msg["timestamp"] === "string" ? msg["timestamp"] : undefined,
      traceId: typeof msg["trace_id"] === "string" ? msg["trace_id"] : undefined,
      spanId: typeof msg["span_id"] === "string" ? msg["span_id"] : undefined,
      receivedAt: Date.now(),
    };
    this.emit("event", ev);
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    const delay = backoffDelay(this.attempt);
    this.attempt += 1;
    this.setStatus({
      state: "reconnecting",
      attempt: this.attempt,
      nextRetryMs: delay,
      lastError: this.status.lastError,
    });
    this.reconnectTimer = setTimeout(() => {
      this.running = false;
      this.connect();
    }, delay);
    this.reconnectTimer.unref();
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.emit("status", status);
  }
}
