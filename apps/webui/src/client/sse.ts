/**
 * SSE client for the Tlon server (browser version, protocol v2).
 *
 * Same flow and outward interface as apps/operator/src/client/sseClient.ts,
 * with the Node fetch stream replaced by the browser `EventSource`:
 * - POST /sessions declares client_type/mode/filters and returns
 *   the session id plus the participants/agents snapshots (emitted as
 *   "session"/"agents"/"participants" events).
 * - PATCH /sessions/{id} switches the session to "event" mode.
 * - GET /events?session_id=... opens the Server-Sent Events stream.
 *   EventSource cannot set custom headers, so the session id always goes
 *   in the query parameter (the server supports both).
 *
 * On stream loss (EventSource `onerror`) the client closes the stream,
 * backs off exponentially (1s -> 30s) and re-establishes the session from
 * scratch. Mail views reconcile durable snapshots once the new stream opens.
 * `dispose()` closes the stream and best-effort DELETEs the session.
 */

import { WEBUI_HEADERS } from "./rest";
import type { ConnectionStatus, ServerEvent } from "../types";

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
  /** Base URL of the server; "" = same-origin relative paths (default). */
  baseUrl?: string;
  clientType?: string;
  filters?: Record<string, unknown>;
}

/** Payload types per emitted event name. */
export interface SseEventMap {
  status: ConnectionStatus;
  session: Record<string, unknown>;
  agents: unknown[];
  participants: { type: "participants"; participants: unknown };
  event: ServerEvent;
  mode_changed: { type: "mode_changed"; mode: SessionMode };
}

export type SseEventName = keyof SseEventMap;

type Listener = (data: never) => void;

/** Minimal typed event emitter (no dependency). */
class Emitter {
  private readonly listeners = new Map<SseEventName, Set<Listener>>();

  on<K extends SseEventName>(event: K, fn: (data: SseEventMap[K]) => void): void {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }
    set.add(fn as Listener);
  }

  off<K extends SseEventName>(event: K, fn: (data: SseEventMap[K]) => void): void {
    this.listeners.get(event)?.delete(fn as Listener);
  }

  protected emit<K extends SseEventName>(event: K, data: SseEventMap[K]): void {
    for (const fn of this.listeners.get(event) ?? []) {
      (fn as (data: SseEventMap[K]) => void)(data);
    }
  }
}

export class SseClient extends Emitter {
  readonly baseUrl: string;
  private sessionId: string | null = null;
  private source: EventSource | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private disposed = false;
  private running = false;
  private generation = 0;
  private status: ConnectionStatus = { state: "disconnected", attempt: 0 };

  constructor(private readonly options: SseClientOptions = {}) {
    super();
    this.baseUrl = options.baseUrl ?? "";
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  getSessionId(): string | null {
    return this.sessionId;
  }

  /**
   * Open a session and start streaming. Safe to call again after
   * `dispose()` (e.g. React StrictMode's unmount/remount cycle): it
   * starts a fresh session from scratch.
   */
  connect(): void {
    if (this.running) return;
    this.disposed = false;
    this.running = true;
    this.setStatus({
      state: this.attempt === 0 ? "connecting" : "reconnecting",
      attempt: this.attempt,
    });
    void this.run(++this.generation);
  }

  /** Switch server-side session mode via PATCH /sessions/{id}. */
  async setMode(mode: SessionMode): Promise<boolean> {
    const sessionId = this.sessionId;
    if (!sessionId) return false;
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...WEBUI_HEADERS },
        body: JSON.stringify({ mode }),
      });
    } catch {
      return false;
    }
    if (!response.ok) return false;
    this.emit("mode_changed", { type: "mode_changed", mode });
    return true;
  }

  /**
   * Idempotent: closes the stream, cancels any pending reconnect and
   * best-effort DELETEs the session (also idempotent server-side).
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation += 1;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.closeStream();
    this.running = false;
    const sessionId = this.sessionId;
    this.sessionId = null;
    if (sessionId) {
      // Best-effort explicit disconnect; the server also cleans up when
      // the closed stream's heartbeat write fails.
      void fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
        headers: WEBUI_HEADERS,
        keepalive: true,
      }).catch(() => {});
    }
    this.setStatus({ state: "disconnected", attempt: 0 });
  }

  /** One connect attempt: open a session, switch to event mode, stream. */
  private async run(generation: number): Promise<void> {
    try {
      await this.openSession(generation);
      if (this.disposed || generation !== this.generation) return;
      const changed = await this.setMode("event");
      if (this.disposed || generation !== this.generation) return;
      if (!changed) throw new Error("Could not subscribe to event mode");
      this.openStream();
    } catch (err) {
      if (this.disposed || generation !== this.generation) return;
      if (!this.disposed) {
        this.status = { ...this.status, lastError: err instanceof Error ? err.message : String(err) };
      }
      this.running = false;
      const sessionId = this.sessionId;
      this.sessionId = null;
      if (sessionId) {
        void fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
          method: "DELETE",
          headers: WEBUI_HEADERS,
        }).catch(() => {});
      }
      this.scheduleReconnect();
    }
  }

  private async openSession(generation: number): Promise<void> {
    const response = await fetch(`${this.baseUrl}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...WEBUI_HEADERS },
      body: JSON.stringify({
        client_type: this.options.clientType ?? "webui",
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
    if (this.disposed || generation !== this.generation) {
      void fetch(`${this.baseUrl}/sessions/${encodeURIComponent(data["session_id"])}`, {
        method: "DELETE",
        headers: WEBUI_HEADERS,
      }).catch(() => {});
      return;
    }
    this.sessionId = data["session_id"];
    this.emit("session", data);
    if (Array.isArray(data["agents"])) this.emit("agents", data["agents"]);
    if (Array.isArray(data["participants"])) {
      this.emit("participants", { type: "participants", participants: data["participants"] });
    }
  }

  /**
   * Open the EventSource stream for the current session. EventSource
   * auto-reconnect is disabled by closing the stream in `onerror`: the
   * server tears the session down when its stream drops, so reconnecting
   * to the same session id is pointless — we rebuild from scratch instead.
   */
  private openStream(): void {
    const sessionId = this.sessionId;
    if (!sessionId || this.disposed) return;
    const source = new EventSource(
      `${this.baseUrl}/events?session_id=${encodeURIComponent(sessionId)}`,
    );
    this.source = source;
    source.onopen = () => {
      if (this.source !== source || this.disposed) return;
      this.attempt = 0;
      this.setStatus({ state: "connected", attempt: 0, sessionId });
    };
    source.onmessage = (msg: MessageEvent<string>) => {
      if (this.source !== source || this.disposed) return;
      this.handleMessage(msg.data);
    };
    source.onerror = () => {
      if (this.source !== source) return; // superseded or disposed
      this.closeStream();
      this.running = false;
      this.sessionId = null;
      if (!this.disposed) {
        this.status = { ...this.status, lastError: "event stream lost" };
        this.scheduleReconnect();
      }
    };
  }

  private closeStream(): void {
    if (this.source) {
      this.source.onerror = null;
      this.source.onopen = null;
      this.source.onmessage = null;
      this.source.close();
      this.source = null;
    }
  }

  /** Dispatch one SSE message; non-event frames are ignored. */
  private handleMessage(raw: string): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw) as Record<string, unknown>;
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
      this.reconnectTimer = null;
      this.running = false;
      this.connect();
    }, delay);
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.emit("status", status);
  }
}
