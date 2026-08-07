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

  /** The member address book (agents + online humans). */
  async listParticipants(): Promise<ParticipantInfo[]> {
    const data = await this.get<{ participants?: ParticipantInfo[] }>("/participants");
    return data.participants ?? [];
  }

  /** In-process metrics snapshot (MetricsService.snapshot()). */
  async getMetrics(): Promise<MetricsSnapshot> {
    return this.get("/metrics");
  }
}
