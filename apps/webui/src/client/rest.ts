/**
 * REST client for the Tlon server (browser version).
 *
 * Same endpoint surface as apps/operator/src/client/restClient.ts (see
 * src/tlon/app/server/api/rest.py), plus the logs endpoints the TUI does
 * not use (`GET /logs`, `GET /agents/{id}/logs`).
 *
 * All requests use same-origin relative paths: in production the app is
 * statically hosted by the server itself (`/ui/`), in dev the vite proxy
 * forwards the API paths to 127.0.0.1:8080.
 */

import type { MetricsSnapshot } from "../lib/snapshot";
import type { MailItem, MailPage, MailReadResult, ParticipantInfo } from "../types";

export class RestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "RestError";
  }
}

/** localStorage key for the admin Bearer token (ADR 0013). */
export const ADMIN_TOKEN_KEY = "tlon.adminToken";

/** Read the persisted admin token; "" when unset or storage unavailable. */
function readStoredAdminToken(): string {
  try {
    return globalThis.localStorage?.getItem(ADMIN_TOKEN_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

/** A mail send request (see `sendMail`). */
export interface MailReceipt {
  mail_id: string;
  request_id: string;
  local_recipients: string[];
  gateways: { address: string; status: string; error?: string | null }[];
  message: string;
}

export interface SendMailMessage {
  request_id?: string;
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
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "MailSendError";
  }
}

/** Query filters for the logs endpoints (all optional, combined). */
export interface LogQuery {
  /** Minimum level (debug/info/warning/error/critical). */
  level?: string;
  agent_id?: string;
  trace_id?: string;
  /** ISO timestamp; only entries logged before it. */
  before?: string;
  limit?: number;
}

/** A structured log entry as written by the server's JSONL logger. */
export type LogEntry = Record<string, unknown>;

export interface LogList {
  logs: LogEntry[];
  count: number;
}

/**
 * Member list entry (GET /agents). Lineage fields (ADR 0006):
 * `spawned_by` is the spawning member's id (null = root member),
 * `spawn_depth` the generation (0 = root). Type alias (not interface)
 * so it stays assignable to Record<string, unknown> for existing
 * consumers.
 */
export type AgentSummary = {
  id: string;
  name: string;
  state: string;
  prototype: string | null;
  spawned_by: string | null;
  spawn_depth: number;
};

/**
 * Member detail (GET /agents/{id}): fresher state plus the config slice
 * the endpoint exposes. The detail payload carries no lineage fields —
 * combine with the AgentSummary row for spawned_by / spawn_depth.
 */
export type AgentDetail = {
  id: string;
  name: string;
  state: string;
  config: { prototype: string | null } & Record<string, unknown>;
};

/** A declared parameter of a prototype (from prototype.tlon). */
export type PrototypeParameter = {
  name: string;
  type: string;
  required: boolean;
};

/**
 * Prototype list entry (GET /admin/prototypes[/staging]).
 * Type alias (not interface) so it stays assignable to
 * Record<string, unknown> for existing consumers.
 */
export type PrototypeSummary = {
  name: string;
  display_name: string;
  description: string;
  trusted: boolean;
  author: string;
  parameters: PrototypeParameter[];
};

/** Prototype detail: list metadata + full behavior.tlon source. */
export type PrototypeDetail = PrototypeSummary & {
  behavior: string;
  /** Derived from the spec's :execution section; null when undeclared or unparseable. */
  execution_level: string | null;
  /** Server-derived review red flags (e.g. staging + L2 declaration). */
  red_flags: string[];
  /** 演武场（staging 详情才有）：最新 run 摘要 + 报告红旗；无报告为 null。 */
  arena?: ArenaLatest | null;
};

/**
 * Arena run record (ArenaRun.to_dict(), docs/design/arena.md). Terminal
 * statuses: done / timed_out / failed.
 */
export type ArenaRun = {
  run_id: string;
  prototype: string;
  model: string;
  params: Record<string, unknown> | null;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  corpus_limit: number;
  time_box_seconds: number;
  fed_count: number;
  completed_count: number;
  timed_out: boolean;
  error: string | null;
  fed: Record<string, unknown>[];
  report_error: string | null;
};

/** One automatic red flag from the arena report (report.json). */
export type ArenaRedFlag = {
  kind: string;
  detail: string;
  evidence: string;
};

/** Staging detail's arena field: latest archived run + its red flags. */
export type ArenaLatest = {
  latest_run: ArenaRun | null;
  red_flags: ArenaRedFlag[];
};

/** GET .../arena-runs/{run_id}/report response: report.json + ARENA.md text. */
export type ArenaRunReport = {
  report: Record<string, unknown>;
  arena_md: string;
};

/** Handwritten prototype creation body (POST /admin/prototypes). */
export type PrototypeCreate = {
  name: string;
  behavior: string;
  display_name?: string;
  description?: string;
};

/** Prototype edit body (PATCH); only changed fields are sent. */
export type PrototypePatch = {
  display_name?: string;
  description?: string;
  behavior?: string;
};

/**
 * LLM registry entry (GET /admin/llm/models, ADR 0016 §4). Prices are
 * USD per million tokens; fields the registry does not carry are null.
 */
export type LlmModelInfo = {
  model: string;
  provider: string;
  context_window: number | null;
  input_price: number | null;
  output_price: number | null;
  cache_read_price: number | null;
  supports_prompt_caching: boolean;
  supports_function_calling: boolean;
  deprecation_date: string | null;
};

/**
 * Capability write rejection (POST staging): like RestError but also carries
 * the server's semantic reason code (400 invalid_capability on a failed
 * validation chain — nothing was written in that case).
 */
export class CapabilityError extends Error {
  constructor(
    message: string,
    readonly reason?: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "CapabilityError";
  }
}

/**
 * Capability list entry (GET /admin/capabilities[/staging],
 * docs/design/capabilities.md). `pure` is null when the artifact failed
 * fail-soft parsing (listing never fails); `deprecated` is the retirement
 * marker (vocabulary only grows — display only, no edit operation).
 */
export type CapabilitySummary = {
  name: string;
  description: string;
  pure: boolean | null;
  trusted: boolean;
  author: string | null;
  proposal_note: string | null;
  deprecated: boolean;
};

/** Capability detail: list metadata + full capability.tlon artifact text. */
export type CapabilityDetail = CapabilitySummary & {
  artifact: string;
  /** REVIEW.md advisory review records (present only when any landed). */
  reviews?: string;
  /**
   * `:examples` assertion outcomes (EDN values rendered as EDN text;
   * `actual` present when the example evaluated, `error` when it did
   * not). Empty when the artifact declares no examples or failed
   * fail-soft parsing.
   */
  examples?: CapabilityExample[];
};

/** One `:examples` assertion outcome (see CapabilityDetail.examples). */
export type CapabilityExample = {
  args: string;
  expect: string;
  passed: boolean;
  actual?: string;
  error?: string;
};

/** Staging proposal creation body (POST /admin/capabilities/staging). */
export type CapabilityCreate = {
  artifact: string;
  proposal_note?: string;
};

/**
 * Prototype write rejection (POST/PATCH): like RestError but also carries
 * the server's semantic reason code (e.g. "invalid_prototype" on 400).
 */
export class PrototypeError extends Error {
  constructor(
    message: string,
    readonly reason?: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "PrototypeError";
  }
}

/** GET /health response (see handle_health in src/tlon/app/server/api/rest.py). */
export interface ServerHealth {
  status: string;
  running: boolean;
  timestamp: string;
  version: string;
}

/**
 * Active client session entry (GET /sessions, see handle_list_sessions
 * in src/tlon/app/server/api/rest.py). Note: the id field is `id`, not
 * `session_id` (that name is only used by POST/PATCH /sessions).
 */
export type SessionInfo = {
  id: string;
  client_type: string;
  connected_at: string;
  mode: string;
  filters: Record<string, unknown>;
};

export interface SessionList {
  sessions: SessionInfo[];
  count: number;
}

export class RestClient {
  readonly baseUrl: string;
  private adminToken: string;

  /**
   * `baseUrl` defaults to "" (same-origin relative paths). `adminToken`
   * defaults to the value persisted in localStorage (ADR 0013); pass an
   * explicit value in tests / non-browser contexts.
   */
  constructor(baseUrl = "", adminToken?: string) {
    this.baseUrl = baseUrl;
    this.adminToken = adminToken ?? readStoredAdminToken();
  }

  /** Whether an admin Bearer token is configured. */
  get hasAdminToken(): boolean {
    return this.adminToken.length > 0;
  }

  /**
   * Set (or clear, with "") the admin Bearer token, persisting it to
   * localStorage like the username. Takes effect on the next request.
   */
  setAdminToken(token: string): void {
    this.adminToken = token.trim();
    try {
      if (this.adminToken) {
        globalThis.localStorage?.setItem(ADMIN_TOKEN_KEY, this.adminToken);
      } else {
        globalThis.localStorage?.removeItem(ADMIN_TOKEN_KEY);
      }
    } catch {
      // Storage unavailable (tests, privacy mode) — in-memory only.
    }
  }

  /** Authorization header for every request when a token is configured. */
  private authHeaders(): Record<string, string> {
    return this.adminToken ? { Authorization: `Bearer ${this.adminToken}` } : {};
  }

  /** 401 means the server wants an admin token — say where to fix it. */
  private static withAuthHint(message: string, status?: number): string {
    return status === 401
      ? `${message} — admin 端点需要 token，请在 Settings 页配置`
      : message;
  }

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, { headers: this.authHeaders() });
    } catch (err) {
      throw new RestError(`REST request failed: ${path} (${String(err)})`);
    }
    if (!response.ok) {
      throw new RestError(
        RestClient.withAuthHint(`REST ${path} returned ${response.status}`, response.status),
        response.status,
      );
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
        headers: {
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...this.authHeaders(),
        },
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
        RestClient.withAuthHint(
          data.error ?? `REST ${method} ${path} returned ${String(response.status)}`,
          response.status,
        ),
        response.status,
      );
    }
    return data;
  }

  async health(): Promise<ServerHealth> {
    return this.get("/health");
  }

  async config(): Promise<Record<string, unknown>> {
    return this.get("/config");
  }

  /** Active client sessions (GET /sessions): which clients are connected. */
  async listSessions(): Promise<SessionList> {
    const data = await this.get<SessionList>("/sessions");
    return { sessions: data.sessions ?? [], count: data.count ?? 0 };
  }

  async listAgents(): Promise<AgentSummary[]> {
    const data = await this.get<{ agents?: AgentSummary[] }>("/agents");
    return data.agents ?? [];
  }

  async getAgent(agentId: string): Promise<AgentDetail> {
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

  async getInboxPage(
    username: string,
    options: { before?: string; limit?: number } = {},
  ): Promise<MailPage> {
    const query = new URLSearchParams({ username, limit: String(options.limit ?? 100) });
    if (options.before) query.set("before", options.before);
    return this.get(`/inbox?${query.toString()}`);
  }

  async getMemberMailPage(
    memberId: string,
    options: { before?: string; limit?: number } = {},
  ): Promise<MailPage> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
    if (options.before) query.set("before", options.before);
    return this.get(`/members/${encodeURIComponent(memberId)}/mail?${query.toString()}`);
  }

  /** Explicitly change only this human's received copies; viewing never marks read. */
  async setMailRead(
    username: string,
    read: boolean,
    target: { mail_id?: string; thread_id?: string },
  ): Promise<MailReadResult> {
    return this.send("POST", "/inbox/read", { username, read, ...target });
  }

  /**
   * Any member's mailbox, read-only observer view
   * (GET /members/{member_id}/mail). Same merge semantics as getInbox,
   * but the payloads carry no `read` flag. Rejects with a RestError
   * (status 404) when the member does not exist.
   */
  async getMemberMail(memberId: string, limit = 100): Promise<MailItem[]> {
    const data = await this.get<{ mails?: MailItem[] }>(
      `/members/${encodeURIComponent(memberId)}/mail?limit=${limit}`,
    );
    return data.mails ?? [];
  }

  /**
   * Send a mail as the given human user (POST /messages). Recipients in
   * `to`/`cc` may be display names, member ids or full addresses — the
   * server resolves them. Resolves with the new mail id; rejects with a
   * MailSendError carrying the server's semantic reason on 4xx.
   */
  async sendMail(username: string, msg: SendMailMessage): Promise<MailReceipt> {
    const requestId = msg.request_id ?? crypto.randomUUID();
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, ...msg, request_id: requestId }),
      });
    } catch (err) {
      throw new MailSendError(`投信结果未知；使用原 request_id ${requestId} 重试 (${String(err)})`, "submission_unknown", undefined, requestId);
    }
    const data = (await response.json().catch(() => ({}))) as {
      mail_id?: string;
      request_id?: string;
      local_recipients?: string[];
      gateways?: MailReceipt["gateways"];
      message?: string;
      error?: string;
      reason?: string;
    };
    if (!response.ok) {
      throw new MailSendError(
        `${data.error ?? `REST /messages returned ${String(response.status)}`} (request_id: ${requestId})`,
        data.reason,
        response.status,
        requestId,
      );
    }
    if (typeof data.mail_id !== "string") {
      throw new MailSendError(`投信结果未知；使用原 request_id ${requestId} 重试`, "submission_unknown", undefined, requestId);
    }
    if (typeof data.request_id !== "string" || typeof data.message !== "string"
        || !Array.isArray(data.local_recipients) || !Array.isArray(data.gateways)) {
      throw new MailSendError(`投信结果未知；使用原 request_id ${requestId} 重试`, "submission_unknown", undefined, requestId);
    }
    return data as MailReceipt;
  }

  /** The member address book (agents + online humans). */
  async listParticipants(): Promise<ParticipantInfo[]> {
    const data = await this.get<{ participants?: ParticipantInfo[] }>("/participants");
    return data.participants ?? [];
  }

  // ---- Admin (wizard removal): agent CRUD + prototype approval ----

  /**
   * Like send(), but rejects with a PrototypeError carrying the server's
   * semantic reason code (prototype writes report 400 invalid_prototype).
   */
  private async prototypeSend<T>(
    method: "POST" | "PATCH",
    path: string,
    body: unknown,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: { "Content-Type": "application/json", ...this.authHeaders() },
        body: JSON.stringify(body),
      });
    } catch (err) {
      throw new PrototypeError(
        `REST request failed: ${method} ${path} (${String(err)})`,
      );
    }
    const data = (await response.json().catch(() => ({}))) as {
      error?: string;
      reason?: string;
    } & T;
    if (!response.ok) {
      throw new PrototypeError(
        RestClient.withAuthHint(
          data.error ?? `REST ${method} ${path} returned ${String(response.status)}`,
          response.status,
        ),
        data.reason,
        response.status,
      );
    }
    return data;
  }

  /** Trusted prototypes a member can be created from (behavior genome). */
  async listPrototypes(): Promise<PrototypeSummary[]> {
    const data = await this.get<{ prototypes?: PrototypeSummary[] }>(
      "/admin/prototypes",
    );
    return data.prototypes ?? [];
  }

  /** LLM-authored prototypes awaiting human approval. */
  async listStaging(): Promise<PrototypeSummary[]> {
    const data = await this.get<{ prototypes?: PrototypeSummary[] }>(
      "/admin/prototypes/staging",
    );
    return data.prototypes ?? [];
  }

  /** Trusted prototype detail: metadata + full behavior.tlon source. */
  async getPrototype(name: string): Promise<PrototypeDetail> {
    const data = await this.get<{ prototype: PrototypeDetail }>(
      `/admin/prototypes/${encodeURIComponent(name)}`,
    );
    return data.prototype;
  }

  /** Staging prototype detail, same shape as getPrototype. */
  async getStagingPrototype(name: string): Promise<PrototypeDetail> {
    const data = await this.get<{ prototype: PrototypeDetail }>(
      `/admin/prototypes/staging/${encodeURIComponent(name)}`,
    );
    return data.prototype;
  }

  /** Handwritten creation: lands directly in the trusted library (201). */
  async createPrototype(body: PrototypeCreate): Promise<Record<string, unknown>> {
    return this.prototypeSend("POST", "/admin/prototypes", body);
  }

  /** Edit a trusted prototype's metadata and/or behavior (re-validated). */
  async updatePrototype(
    name: string,
    body: PrototypePatch,
  ): Promise<Record<string, unknown>> {
    return this.prototypeSend(
      "PATCH",
      `/admin/prototypes/${encodeURIComponent(name)}`,
      body,
    );
  }

  /** Edit a staging prototype before approval, same semantics. */
  async updateStagingPrototype(
    name: string,
    body: PrototypePatch,
  ): Promise<Record<string, unknown>> {
    return this.prototypeSend(
      "PATCH",
      `/admin/prototypes/staging/${encodeURIComponent(name)}`,
      body,
    );
  }

  /** Delete a trusted prototype (staging deletion is reject). */
  async deletePrototype(name: string): Promise<Record<string, unknown>> {
    return this.send("DELETE", `/admin/prototypes/${encodeURIComponent(name)}`);
  }

  async approvePrototype(name: string): Promise<Record<string, unknown>> {
    return this.send("POST", `/admin/prototypes/staging/${encodeURIComponent(name)}/approve`);
  }

  async rejectPrototype(name: string): Promise<Record<string, unknown>> {
    return this.send("POST", `/admin/prototypes/staging/${encodeURIComponent(name)}/reject`);
  }

  // ---- Arena (docs/design/arena.md): human-only trigger + polling + report ----

  /**
   * Trigger an arena run for a staging prototype (202; the run advances in
   * a background task — poll `getArenaRun` until a terminal status). Body
   * is all-optional; an empty model falls back to TLON_ARENA_MODEL server-side.
   */
  async startArenaRun(name: string, opts: { model?: string } = {}): Promise<ArenaRun> {
    const data = await this.send<{ run: ArenaRun }>(
      "POST",
      `/admin/prototypes/staging/${encodeURIComponent(name)}/arena-runs`,
      opts,
    );
    return data.run;
  }

  /** All arena run records, oldest first (GET /admin/arena-runs). */
  async listArenaRuns(): Promise<ArenaRun[]> {
    const data = await this.get<{ runs?: ArenaRun[] }>("/admin/arena-runs");
    return data.runs ?? [];
  }

  /** One run's live status (GET /admin/arena-runs/{run_id}). */
  async getArenaRun(runId: string): Promise<ArenaRun> {
    const data = await this.get<{ run: ArenaRun }>(
      `/admin/arena-runs/${encodeURIComponent(runId)}`,
    );
    return data.run;
  }

  /** The archived report.json + ARENA.md of one run. */
  async getArenaReport(name: string, runId: string): Promise<ArenaRunReport> {
    return this.get(
      `/admin/prototypes/staging/${encodeURIComponent(name)}/arena-runs/${encodeURIComponent(runId)}/report`,
    );
  }

  // ---- Capabilities (docs/design/capabilities.md): trusted library + staging channel ----

  /**
   * Like send(), but rejects with a CapabilityError carrying the server's
   * semantic reason code (staging writes report 400 invalid_capability).
   */
  private async capabilitySend<T>(
    method: "POST",
    path: string,
    body?: unknown,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: {
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...this.authHeaders(),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      throw new CapabilityError(
        `REST request failed: ${method} ${path} (${String(err)})`,
      );
    }
    const data = (await response.json().catch(() => ({}))) as {
      error?: string;
      message?: string;
      reason?: string;
    } & T;
    if (!response.ok) {
      throw new CapabilityError(
        RestClient.withAuthHint(
          data.error ?? data.message ?? `REST ${method} ${path} returned ${String(response.status)}`,
          response.status,
        ),
        data.reason,
        response.status,
      );
    }
    return data;
  }

  /** Trusted capability words (the library every member may compose). */
  async listCapabilities(): Promise<CapabilitySummary[]> {
    const data = await this.get<{ capabilities?: CapabilitySummary[] }>(
      "/admin/capabilities",
    );
    return data.capabilities ?? [];
  }

  /** Staged capability proposals awaiting human approval. */
  async listStagingCapabilities(): Promise<CapabilitySummary[]> {
    const data = await this.get<{ capabilities?: CapabilitySummary[] }>(
      "/admin/capabilities/staging",
    );
    return data.capabilities ?? [];
  }

  /** Trusted capability detail: metadata + full capability.tlon source. */
  async getCapability(name: string): Promise<CapabilityDetail> {
    const data = await this.get<{ capability: CapabilityDetail }>(
      `/admin/capabilities/${encodeURIComponent(name)}`,
    );
    return data.capability;
  }

  /** Staged capability detail, same shape as getCapability. */
  async getStagingCapability(name: string): Promise<CapabilityDetail> {
    const data = await this.get<{ capability: CapabilityDetail }>(
      `/admin/capabilities/staging/${encodeURIComponent(name)}`,
    );
    return data.capability;
  }

  /** Stage a capability artifact (201; 400 invalid_capability writes nothing). */
  async createStagingCapability(body: CapabilityCreate): Promise<Record<string, unknown>> {
    return this.capabilitySend("POST", "/admin/capabilities/staging", body);
  }

  /** Promote a staged capability to the trusted library. */
  async approveCapability(name: string): Promise<Record<string, unknown>> {
    return this.capabilitySend(
      "POST",
      `/admin/capabilities/staging/${encodeURIComponent(name)}/approve`,
    );
  }

  /** Delete a staged capability (human rejection). */
  async rejectCapability(name: string): Promise<Record<string, unknown>> {
    return this.capabilitySend(
      "POST",
      `/admin/capabilities/staging/${encodeURIComponent(name)}/reject`,
    );
  }

  /** Create a member from a prototype. `persona` is seeded here at creation;
   * afterwards it is member-owned (self-editable via updatePersona, ADR 0024) —
   * humans can still edit via REST PATCH or `tlon mirror`.
   * `model` is required (ADR 0016): provider/model form, member-level. */
  async createAgent(body: {
    prototype: string;
    name: string;
    model: string;
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

  /** Chat model metadata from the litellm registry (GET /admin/llm/models). */
  async listLlmModels(): Promise<LlmModelInfo[]> {
    const data = await this.get<{ models?: LlmModelInfo[] }>("/admin/llm/models");
    return data.models ?? [];
  }

  /** In-process metrics snapshot (MetricsService.snapshot()). */
  async getMetrics(): Promise<MetricsSnapshot> {
    return this.get("/metrics");
  }

  // ---- Logs (webui-only views; the TUI does not use these) ----

  /** All server logs with combined filters (GET /logs). */
  async getLogs(query: LogQuery = {}): Promise<LogList> {
    const params = new URLSearchParams();
    if (query.level) params.set("level", query.level);
    if (query.agent_id) params.set("agent_id", query.agent_id);
    if (query.trace_id) params.set("trace_id", query.trace_id);
    if (query.before) params.set("before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const qs = params.toString();
    const data = await this.get<LogList>(`/logs${qs ? `?${qs}` : ""}`);
    return { logs: data.logs ?? [], count: data.count ?? 0 };
  }

  /** One agent's log entries (GET /agents/{id}/logs). */
  async getAgentLogs(
    agentId: string,
    query: Pick<LogQuery, "before" | "limit"> = {},
  ): Promise<LogList> {
    const params = new URLSearchParams();
    if (query.before) params.set("before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const qs = params.toString();
    const data = await this.get<LogList>(
      `/agents/${encodeURIComponent(agentId)}/logs${qs ? `?${qs}` : ""}`,
    );
    return { logs: data.logs ?? [], count: data.count ?? 0 };
  }
}
