/**
 * WebSocket client for the Tlon server (`ws://host:port/ws`).
 *
 * Protocol (see src/tlon/app/server/api/websocket.py):
 * - On connect the client must send an auth message first:
 *   {"type": "auth", "client_type": "tui", "protocol_version": "1.0.0",
 *    "capabilities": [...], "filters": {...}}
 * - The server replies with {"type": "auth_success", "session_id": ...} and
 *   then pushes initial data plus real-time {"type": "event", ...} messages.
 * - The client sends periodic {"type": "ping", "seq", "timestamp"} heartbeats
 *   (the server also sends protocol-level pings every 30s).
 * - On disconnect the client reconnects with exponential backoff and
 *   re-authenticates on every new connection.
 */

import { EventEmitter } from "node:events";
import type { ConnectionStatus, ServerEvent } from "../types.js";

export const PROTOCOL_VERSION = "1.0.0";
export const PING_INTERVAL_MS = 25_000;
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

/**
 * Minimal structural interface over the global (undici) WebSocket, so we do
 * not depend on DOM lib types. Node >= 22 ships a stable global WebSocket.
 */
interface WsLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  addEventListener(type: "open", listener: () => void): void;
  addEventListener(type: "message", listener: (event: { data: unknown }) => void): void;
  addEventListener(type: "close", listener: () => void): void;
  addEventListener(type: "error", listener: (event: { message?: string }) => void): void;
}

export interface WsClientOptions {
  host: string;
  port: number;
  clientType?: string;
  capabilities?: string[];
  filters?: Record<string, unknown>;
  /** Human username for auth (server defaults to "guest" when omitted). */
  username?: string;
}

/** A mail send request (see `sendMail`). */
export interface SendMailMessage {
  to: string[];
  cc?: string[];
  subject?: string;
  content: string;
  in_reply_to?: string;
  thread_id?: string;
}

export class WsClient extends EventEmitter {
  readonly url: string;
  private readonly authPayload: Record<string, unknown>;
  private ws: WsLike | null = null;
  private pingTimer: NodeJS.Timeout | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pingSeq = 0;
  private attempt = 0;
  private disposed = false;
  private status: ConnectionStatus = { state: "disconnected", attempt: 0 };

  constructor(options: WsClientOptions) {
    super();
    this.url = `ws://${options.host}:${options.port}/ws`;
    this.authPayload = {
      type: "auth",
      client_type: options.clientType ?? "tui",
      protocol_version: PROTOCOL_VERSION,
      capabilities: options.capabilities ?? ["trace", "filter"],
      filters: options.filters ?? {},
      ...(options.username !== undefined ? { username: options.username } : {}),
    };
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  connect(): void {
    if (this.disposed) return;
    this.setStatus({ state: this.attempt === 0 ? "connecting" : "reconnecting", attempt: this.attempt });

    let ws: WsLike;
    try {
      ws = new WebSocket(this.url) as unknown as WsLike;
    } catch (err) {
      this.setStatus({ state: "reconnecting", attempt: this.attempt, lastError: String(err) });
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.setStatus({ state: "authenticating", attempt: this.attempt });
      this.send(this.authPayload);
    });

    ws.addEventListener("message", (event) => {
      this.handleMessage(event.data);
    });

    ws.addEventListener("error", (event) => {
      // A close event always follows; just record the error.
      this.status = { ...this.status, lastError: event.message ?? "WebSocket error" };
      this.emit("status", this.status);
    });

    ws.addEventListener("close", () => {
      this.stopPing();
      this.ws = null;
      if (this.disposed) {
        this.setStatus({ state: "disconnected", attempt: 0 });
        return;
      }
      this.scheduleReconnect();
    });
  }

  /** Send a JSON message if the socket is open. Returns false otherwise. */
  send(message: Record<string, unknown>): boolean {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  /** Switch server-side session mode ("chat" | "event" | "agent"). */
  setMode(mode: "chat" | "event" | "agent"): boolean {
    return this.send({ type: "mode", mode });
  }

  /**
   * Send a mail (`{"type": "message", ...}`). Recipients in `to`/`cc` may be
   * display names, member ids or full addresses — the server resolves them.
   * Send failures arrive asynchronously as a "server_error" event.
   */
  sendMail(msg: SendMailMessage): boolean {
    const frame: Record<string, unknown> = {
      type: "message",
      content: msg.content,
      to: msg.to,
    };
    if (msg.cc && msg.cc.length > 0) frame["cc"] = msg.cc;
    if (msg.subject) frame["subject"] = msg.subject;
    if (msg.in_reply_to) frame["in_reply_to"] = msg.in_reply_to;
    if (msg.thread_id) frame["thread_id"] = msg.thread_id;
    return this.send(frame);
  }

  dispose(): void {
    this.disposed = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  private handleMessage(data: unknown): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(String(data)) as Record<string, unknown>;
    } catch {
      return;
    }

    switch (msg["type"]) {
      case "auth_success": {
        this.attempt = 0;
        this.setStatus({
          state: "connected",
          attempt: 0,
          sessionId: typeof msg["session_id"] === "string" ? msg["session_id"] : undefined,
        });
        this.startPing();
        this.emit("authenticated", msg);
        break;
      }
      case "auth_failed": {
        this.setStatus({
          state: "disconnected",
          attempt: this.attempt,
          lastError: `Auth failed: ${String(msg["message"] ?? "unknown")}`,
        });
        this.emit("auth_failed", msg);
        break;
      }
      case "event": {
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
        break;
      }
      case "agents":
        this.emit("agents", msg["agents"]);
        break;
      case "history":
      case "participants":
      case "mode_changed":
      case "filters_updated":
      case "pong":
        this.emit(msg["type"] as string, msg);
        break;
      case "error":
        this.emit("server_error", msg);
        break;
      default:
        break;
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      this.pingSeq += 1;
      this.send({ type: "ping", seq: this.pingSeq, timestamp: Date.now() });
    }, PING_INTERVAL_MS);
    this.pingTimer.unref();
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
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
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
    this.reconnectTimer.unref();
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.emit("status", status);
  }
}
