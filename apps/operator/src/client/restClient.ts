/**
 * REST client for the Tlon server (`http://host:port`).
 *
 * Endpoints (see src/tlon/app/server/api/rest.py):
 * - GET /health
 * - GET /config
 * - GET /agents, GET /agents/{id}
 * - GET /traces, GET /traces/{id}
 * - GET /admin/config/agents
 * - GET /inbox?username=x&limit=N
 * - POST /messages
 * - GET /participants
 * - GET /metrics
 *
 * Uses the Node >= 22 global fetch.
 */

import type { MetricsSnapshot } from "../metrics/snapshot.js";
import type { MailItem, ParticipantInfo } from "../types.js";

export class RestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "RestError";
  }
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

/** POST /messages rejection: carries the server's semantic reason code. */
export class MailSendError extends Error {
  constructor(
    message: string,
    readonly reason?: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "MailSendError";
  }
}

export class RestClient {
  readonly baseUrl: string;

  constructor(host: string, port: number) {
    this.baseUrl = `http://${host}:${port}`;
  }

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`);
    } catch (err) {
      throw new RestError(`REST request failed: ${path} (${String(err)})`);
    }
    if (!response.ok) {
      throw new RestError(`REST ${path} returned ${response.status}`, response.status);
    }
    return (await response.json()) as T;
  }

  private async send<T>(
    method: "POST" | "PATCH" | "PUT" | "DELETE",
    path: string,
    body?: unknown,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      throw new RestError(`REST request failed: ${method} ${path} (${String(err)})`);
    }
    const data = (await response.json().catch(() => ({}))) as {
      error?: string;
      reason?: string;
    } & T;
    if (!response.ok) {
      throw new RestError(
        data.error ?? `REST ${method} ${path} returned ${String(response.status)}`,
        response.status,
      );
    }
    return data;
  }

  async health(): Promise<Record<string, unknown>> {
    return this.get("/health");
  }

  async config(): Promise<Record<string, unknown>> {
    return this.get("/config");
  }

  async listAgents(): Promise<Record<string, unknown>[]> {
    const data = await this.get<{ agents?: Record<string, unknown>[] }>("/agents");
    return data.agents ?? [];
  }

  async getAgent(agentId: string): Promise<Record<string, unknown>> {
    return this.get(`/agents/${encodeURIComponent(agentId)}`);
  }

  async listTraces(limit = 100): Promise<Record<string, unknown>[]> {
    const data = await this.get<{ traces?: Record<string, unknown>[] }>(`/traces?limit=${limit}`);
    return data.traces ?? [];
  }

  async getTrace(traceId: string): Promise<Record<string, unknown>> {
    return this.get(`/traces/${encodeURIComponent(traceId)}`);
  }

  async listAgentConfigs(): Promise<Record<string, unknown>[]> {
    const data = await this.get<{ agents?: Record<string, unknown>[] }>("/admin/config/agents");
    return data.agents ?? [];
  }

  /** The member's own mail (inbox + sent merged, chronological). */
  async getInbox(username: string, limit = 100): Promise<MailItem[]> {
    const data = await this.get<{ mails?: MailItem[] }>(
      `/inbox?username=${encodeURIComponent(username)}&limit=${limit}`,
    );
    return data.mails ?? [];
  }

  /**
   * Send a mail as the given human user (POST /messages). Recipients in
   * `to`/`cc` may be display names, member ids or full addresses — the
   * server resolves them. Resolves with the new mail id; rejects with a
   * MailSendError carrying the server's semantic reason on 4xx.
   */
  async sendMail(username: string, msg: SendMailMessage): Promise<string> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, ...msg }),
      });
    } catch (err) {
      throw new MailSendError(`REST request failed: /messages (${String(err)})`);
    }
    const data = (await response.json().catch(() => ({}))) as {
      mail_id?: string;
      error?: string;
      reason?: string;
    };
    if (!response.ok) {
      throw new MailSendError(
        data.error ?? `REST /messages returned ${String(response.status)}`,
        data.reason,
        response.status,
      );
    }
    if (typeof data.mail_id !== "string") {
      throw new MailSendError("REST /messages: response missing mail_id");
    }
    return data.mail_id;
  }

  /** The member address book (agents + online humans). */
  async listParticipants(): Promise<ParticipantInfo[]> {
    const data = await this.get<{ participants?: ParticipantInfo[] }>("/participants");
    return data.participants ?? [];
  }

  // ---- Admin (wizard removal): agent CRUD + prototype approval ----

  /** Trusted prototypes a member can be created from (behavior genome). */
  async listPrototypes(): Promise<Record<string, unknown>[]> {
    const data = await this.get<{ prototypes?: Record<string, unknown>[] }>(
      "/admin/prototypes",
    );
    return data.prototypes ?? [];
  }

  /** LLM-authored prototypes awaiting human approval. */
  async listStaging(): Promise<Record<string, unknown>[]> {
    const data = await this.get<{ prototypes?: Record<string, unknown>[] }>(
      "/admin/prototypes/staging",
    );
    return data.prototypes ?? [];
  }

  async approvePrototype(name: string): Promise<Record<string, unknown>> {
    return this.send("POST", `/admin/prototypes/staging/${encodeURIComponent(name)}/approve`);
  }

  async rejectPrototype(name: string): Promise<Record<string, unknown>> {
    return this.send("POST", `/admin/prototypes/staging/${encodeURIComponent(name)}/reject`);
  }

  /** Create a member from a prototype (persona = creation-time data). */
  async createAgent(body: {
    prototype: string;
    name: string;
    agent_id?: string;
    params?: Record<string, unknown>;
    persona?: Record<string, string>;
  }): Promise<Record<string, unknown>> {
    return this.send("POST", "/admin/config/agents", body);
  }

  /** Edit a member's persona / LLM model. */
  async updateAgent(
    agentId: string,
    body: {
      persona?: Record<string, string>;
      llm?: { model_name?: string };
    },
  ): Promise<Record<string, unknown>> {
    return this.send("PATCH", `/admin/config/agents/${encodeURIComponent(agentId)}`, body);
  }

  /** Administratively remove a member (records + home archive deleted). */
  async deleteAgent(agentId: string): Promise<Record<string, unknown>> {
    return this.send("DELETE", `/admin/config/agents/${encodeURIComponent(agentId)}`);
  }

  /** In-process metrics snapshot (MetricsService.snapshot()). */
  async getMetrics(): Promise<MetricsSnapshot> {
    return this.get("/metrics");
  }
}
