/**
 * AGENTS view — member card grid (state-colored, with lineage) +
 * structured detail panel (with the member's recent logs embedded,
 * GET /agents/{id}/logs), create (pick a prototype -> form), edit,
 * delete (confirm). The model field in the create/edit forms is a
 * searchable combobox fed by GET /admin/llm/models (ADR 0016 §4);
 * registry-unlisted values stay allowed as free text.
 *
 * Cards carry an event-driven live status line (lib/agentLiveStatus.ts):
 * Last LLM call duration + model and most recent tool call. Structural changes
 * (spawned/stopped/…) and config.updated still trigger a full reload.
 *
 * Ported from apps/operator/src/views/AgentsView.tsx: same REST/SSE semantics
 * (GET /agents[/{id}], /admin/config/agents CRUD, SSE agents snapshot +
 * agent* events), rendering layer rewritten to DOM. Staging prototype
 * approval moved to the PROTOTYPES view (2026-08-21 decision).
 *
 * Lineage (ADR 0006): the list payload carries prototype / spawned_by /
 * spawn_depth; the detail endpoint does not, so the detail panel
 * combines the list row with the GET /agents/{id} response.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import type { AgentSchedulingResponse, AgentConfig, AgentPersona, AgentDetail, AgentSummary, LlmModelInfo, LogEntry, RestClient } from "../client/rest";
import { wakeLabel } from "../lib/agentState";
import { AgentSchedulingActions } from "../components/AgentSchedulingActions";
import { pendingSchedulingLabel, schedulingNotice, sleepDeadline } from "../lib/agentScheduling";
import type { SchedulingAction } from "../lib/agentScheduling";
import { SchedulingInfo, StateBadge } from "../components/AgentSchedulingState";
import { buildAgentUpdate, PERSONA_FIELDS } from "../lib/agentConfig";
import { formatDurationMs } from "../lib/activity";
import { applyEvent } from "../lib/agentLiveStatus";
import type { AgentLiveStatus, LiveStatusMap } from "../lib/agentLiveStatus";
import { formatTimestamp, levelClass, logJson, logMessage, strField } from "../lib/logs";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/agents.css";
import "../styles/logs.css";

type ProtoRecord = Record<string, unknown>;

type Modal =
  | { kind: "create-proto" }
  | { kind: "create-form"; prototype: string }
  | { kind: "edit"; agentId: string }
  | { kind: "delete"; agentId: string }
  | { kind: "terminate"; agentId: string }
  | { kind: "sleep"; agentId: string };

function protoLabel(p: ProtoRecord): string {
  const name = String(p["name"] ?? "?");
  const display = String(p["display_name"] ?? "");
  return display && display !== name ? `${display} — ${name}` : name;
}

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function AgentsView({ rest, sse }: ViewProps): ReactElement {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [live, setLive] = useState<LiveStatusMap>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [busyAgent, setBusyAgent] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await rest.listAgents();
      setAgents(list);
      setError(null);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setLoading(false);
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  // Real-time refresh: the session's agents snapshot replaces the list;
  // any agent* event (or a config change) triggers a reload. Every event
  // also feeds the per-agent history reducer (LLM/tool).
  useEffect(() => {
    const onAgents = (list: unknown[]): void => {
      setAgents(list as AgentSummary[]);
      setError(null);
    };
    const onEvent = (ev: ServerEvent): void => {
      setLive((prev) => applyEvent(prev, ev));
      if (ev.eventType.startsWith("agent") || ev.eventType === "config.updated") {
        void load();
      }
    };
    sse.on("agents", onAgents);
    sse.on("event", onEvent);
    return () => {
      sse.off("agents", onAgents);
      sse.off("event", onEvent);
    };
  }, [sse, load]);

  /** Toggle the detail panel; fetches GET /agents/{id} on expand. */
  const selectAgent = (id: string): void => {
    if (selectedId === id) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    setSelectedId(id);
    setDetail(null);
    setDetailLoading(true);
    rest
      .getAgent(id)
      .then(setDetail)
      .catch((err: unknown) => setError(errMsg(err)))
      .finally(() => setDetailLoading(false));
  };

  /** Shared post-action wrap-up: close modal, notice, reload. */
  const onActionDone = (msg: string, result?: AgentSchedulingResponse): void => {
    if (result) setAgents((current) => current.map((agent) => agent.id === result.agent_id ? { ...agent, ...result.agent } : agent));
    setModal(null);
    setNotice(msg);
    setError(null);
    void load();
  };

  const schedule = async (agentId: string, action: SchedulingAction): Promise<void> => {
    setNotice(null);
    if (action === "sleep" || action === "terminate") {
      setModal({ kind: action, agentId });
      return;
    }
    setBusyAgent(agentId);
    try {
      const result = action === "wake" ? await rest.wakeAgent(agentId) : await rest.waitAgent(agentId);
      onActionDone(schedulingNotice(action, result), result);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusyAgent(null);
    }
  };

  return (
    <div className="agents-view">
      <div className="agents-toolbar">
        <h2 className="tech-label agents-title">AGENTS</h2>
        <span className="tech-label">{agents.length} MEMBERS</span>
        <span className="agents-spacer" />
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => {
            setNotice(null);
            void load();
          }}
        >
          REFRESH
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => setModal({ kind: "create-proto" })}
        >
          + CREATE
        </button>
      </div>

      {error ? <div className="agents-error-bar">{error}</div> : null}
      {notice ? <div className="agents-notice">{notice}</div> : null}

      {loading ? (
        <p className="agents-empty">LOADING…</p>
      ) : agents.length === 0 ? (
        <p className="agents-empty">NO AGENTS</p>
      ) : (
        <div className="agents-grid">
          {agents.map((agent) => {
            const id = agent.id;
            return (
              <div
                key={id}
                className={`agent-card${selectedId === id ? " agent-card--selected" : ""}`}
                onClick={() => selectAgent(id)}
              >
                <div className="agent-card-head">
                  <span className="agent-card-id">{id}</span>
                  <StateBadge state={agent.state} />
                </div>
                {agent.name !== id ? (
                  <div className="tech-label agent-card-name">{agent.name}</div>
                ) : null}
                <div className="tech-label agent-card-meta">
                  {agent.prototype ?? "MANUAL"} · GEN {agent.spawn_depth}
                </div>
                <SchedulingInfo status={agent} />
                <AgentLiveLine status={live[id]} />
                <div className="agent-card-actions">
                  <AgentSchedulingActions status={agent} busy={busyAgent === id} onAction={(action) => void schedule(id, action)} />
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={(e) => {
                      e.stopPropagation();
                      setModal({ kind: "edit", agentId: id });
                    }}
                  >
                    EDIT
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger"
                    onClick={(e) => {
                      e.stopPropagation();
                      setModal({ kind: "delete", agentId: id });
                    }}
                  >
                    DELETE
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selectedId ? (
        <section className="panel agent-detail">
          <div className="agent-detail-head">
            <span className="tech-label">DETAIL // {selectedId}</span>
            <span className="agents-spacer" />
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => {
                setSelectedId(null);
                setDetail(null);
              }}
            >
              CLOSE
            </button>
          </div>
          {detailLoading ? (
            <p className="agents-empty">LOADING…</p>
          ) : detail ? (
            <AgentDetailPanel
              detail={detail}
              listItem={agents.find((a) => a.id === selectedId)}
            />
          ) : (
            <p className="agents-empty">NO DETAIL</p>
          )}
          <MemberLogs rest={rest} agentId={selectedId} />
        </section>
      ) : null}

      {modal?.kind === "create-proto" ? (
        <CreateProtoModal
          rest={rest}
          onPick={(name) => setModal({ kind: "create-form", prototype: name })}
          onClose={() => setModal(null)}
        />
      ) : null}
      {modal?.kind === "create-form" ? (
        <CreateFormModal
          rest={rest}
          prototype={modal.prototype}
          onDone={onActionDone}
          onClose={() => setModal(null)}
        />
      ) : null}
      {modal?.kind === "edit" ? (
        <EditModal
          rest={rest}
          agentId={modal.agentId}
          onDone={onActionDone}
          onClose={() => setModal(null)}
        />
      ) : null}
      {modal?.kind === "sleep" ? <SleepModal rest={rest} agentId={modal.agentId} onDone={onActionDone} onClose={() => setModal(null)} /> : null}
      {modal?.kind === "terminate" ? (
        <TerminateModal rest={rest} agentId={modal.agentId} onDone={onActionDone} onClose={() => setModal(null)} />
      ) : null}
      {modal?.kind === "delete" ? (
        <DeleteModal
          rest={rest}
          agentId={modal.agentId}
          onDone={(msg) => {
            setSelectedId((prev) => (prev === modal.agentId ? null : prev));
            setDetail(null);
            onActionDone(msg);
          }}
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

/** Two reserved history rows keep event updates from resizing cards. */
function AgentLiveLine({ status }: { status?: AgentLiveStatus }): ReactElement {
  const llm = status?.lastLlmMs !== undefined
    ? `LLM ${formatDurationMs(status.lastLlmMs)}${status.lastLlmModel ? ` · ${status.lastLlmModel}` : ""}`
    : "LLM —";
  const tool = status?.lastTool;
  const toolLabel = tool ? `${tool.name} ${tool.success ? "✓" : "✗"}` : "TOOL —";
  return (
    <div className="tech-label agent-card-live">
      <span className="live-item" title={llm}>{llm}</span>
      <span
        className={`live-item${tool ? ` ${tool.success ? "live-tool--ok" : "live-tool--fail"}` : ""}`}
        title={toolLabel}
      >
        {toolLabel}
      </span>
    </div>
  );
}

/** One labeled value row in the member detail panel. */
function DetailRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): ReactElement {
  return (
    <div className="agent-detail-row">
      <span className="tech-label agent-detail-label">{label}</span>
      <span className="agent-detail-value">{children}</span>
    </div>
  );
}

/**
 * Structured member detail: state/name/config from GET /agents/{id},
 * lineage (prototype / spawned_by / spawn_depth) from the list row —
 * the detail endpoint does not carry those fields.
 */
function AgentDetailPanel({
  detail,
  listItem,
}: {
  detail: AgentDetail;
  listItem?: AgentSummary;
}): ReactElement {
  const prototype = listItem?.prototype ?? detail.config.prototype ?? "MANUAL";
  const spawnedBy = listItem?.spawned_by ?? null;
  const spawnDepth = listItem?.spawn_depth;
  // config.prototype is already a top-level field; show the rest.
  const configEntries = Object.entries(detail.config).filter(
    ([key]) => key !== "prototype",
  );
  return (
    <div className="agent-detail-fields">
      <DetailRow label="STATE">
        <StateBadge state={listItem?.state ?? detail.state} />
      </DetailRow>
      {(listItem?.state ?? detail.state) === "sleeping" ? <DetailRow label="WAKE_AT">{wakeLabel(listItem?.wake_at ?? detail.wake_at)}</DetailRow> : null}
      <DetailRow label="RUNTIME">{(listItem?.resident ?? detail.resident) ? "RESIDENT" : "UNLOADED"}</DetailRow>
      {pendingSchedulingLabel(listItem ?? detail) ? <DetailRow label="待执行操作">{pendingSchedulingLabel(listItem ?? detail)}</DetailRow> : null}
      <DetailRow label="NAME">{detail.name}</DetailRow>
      <DetailRow label="PROTOTYPE">{prototype}</DetailRow>
      <DetailRow label="SPAWNED_BY">{spawnedBy ?? "ROOT // FIRST GENERATION"}</DetailRow>
      <DetailRow label="SPAWN_DEPTH">
        {spawnDepth !== undefined ? `GEN ${spawnDepth}` : "—"}
      </DetailRow>
      {configEntries.length > 0 ? (
        <div className="agent-detail-config">
          <span className="tech-label agent-detail-label">CONFIG</span>
          {configEntries.map(([key, value]) => (
            <DetailRow key={key} label={key}>
              {typeof value === "object" && value !== null
                ? JSON.stringify(value)
                : String(value)}
            </DetailRow>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const MEMBER_LOGS_LIMIT = 20;

/**
 * Recent logs of one member (GET /agents/{id}/logs), embedded in the
 * detail panel. Same row rendering as the LOGS view — timestamp,
 * colored level, message; clicking a row expands the entry's full JSON.
 * Newest entries sit at the bottom.
 */
function MemberLogs({
  rest,
  agentId,
}: {
  rest: RestClient;
  agentId: string;
}): ReactElement {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<LogEntry | null>(null);
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setExpanded(null);
    rest
      .getAgentLogs(agentId, { limit: MEMBER_LOGS_LIMIT })
      .then((data) => {
        if (cancelled) return;
        setLogs(data.logs);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errMsg(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rest, agentId]);

  // The log list is chronological; stick to the newest entries.
  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  return (
    <div className="agent-logs">
      <span className="tech-label agent-detail-label">RECENT LOGS</span>
      {error ? <div className="agents-error-bar">{error}</div> : null}
      <div className="terminal agent-logs-terminal" ref={termRef}>
        {loading ? (
          <div className="terminal-line logs-empty">loading…</div>
        ) : logs.length === 0 ? (
          <div className="terminal-line logs-empty">no log entries</div>
        ) : null}
        {logs.map((entry, index) => {
          const entryLevel = (strField(entry, "level") ?? "info").toLowerCase();
          return (
            <div key={index}>
              <div
                className={`terminal-line log-row${expanded === entry ? " log-row--expanded" : ""}`}
                onClick={() => setExpanded(expanded === entry ? null : entry)}
              >
                <span className="log-dim">{formatTimestamp(strField(entry, "timestamp"))} </span>
                <span className={levelClass(entryLevel)}>
                  {entryLevel.toUpperCase().padEnd(7)}
                </span>{" "}
                <span className="log-msg">{logMessage(entry)}</span>
              </div>
              {expanded === entry ? <pre className="log-json">{logJson(entry)}</pre> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- model combobox (ADR 0016 §4) ----

/** Module-level cache: the registry is static for the server's lifetime. */
let llmModelsPromise: Promise<LlmModelInfo[]> | null = null;

function getLlmModels(rest: RestClient): Promise<LlmModelInfo[]> {
  llmModelsPromise ??= rest.listLlmModels();
  return llmModelsPromise;
}

/** Human-readable context window: 131072 -> "128K". */
function formatContextWindow(tokens: number | null): string {
  if (tokens === null) return "—";
  return tokens >= 1024 ? `${Math.round(tokens / 1024)}K` : String(tokens);
}

/** Human-readable per-million-token price: 0.28 -> "$0.28". */
function formatPrice(price: number | null): string {
  return price === null ? "—" : `$${price.toFixed(2)}`;
}

/** Metadata summary line under a model name in the dropdown. */
function modelMetaLine(m: LlmModelInfo): string {
  const parts = [`CTX ${formatContextWindow(m.context_window)}`];
  parts.push(`IN ${formatPrice(m.input_price)}/M`);
  parts.push(`OUT ${formatPrice(m.output_price)}/M`);
  if (m.cache_read_price !== null) parts.push(`CACHE ${formatPrice(m.cache_read_price)}/M`);
  if (m.supports_prompt_caching) parts.push("+cache");
  if (m.supports_function_calling) parts.push("+tools");
  return parts.join(" · ");
}

const MODEL_DROPDOWN_LIMIT = 50;

/**
 * Searchable model combobox fed by GET /admin/llm/models. The input stays
 * a free-text field — a value absent from the registry (private/custom
 * model) is kept as-is; the dropdown is assistive, not a constraint.
 */
function ModelSelect({
  rest,
  value,
  onChange,
}: {
  rest: RestClient;
  value: string;
  onChange: (model: string) => void;
}): ReactElement {
  const [models, setModels] = useState<LlmModelInfo[] | null>(null);
  const [open, setOpen] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLlmModels(rest)
      .then((list) => {
        if (!cancelled) setModels(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setFetchError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
  }, [rest]);

  const query = value.trim().toLowerCase();
  const filtered = (models ?? [])
    .filter(
      (m) =>
        m.model.toLowerCase().includes(query) || m.provider.toLowerCase().includes(query),
    )
    .slice(0, MODEL_DROPDOWN_LIMIT);
  const known = models?.some((m) => m.model === value.trim()) ?? true;

  return (
    <div className="model-select">
      <input
        className="input"
        value={value}
        placeholder="provider/model, e.g. deepseek/deepseek-chat"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
      />
      {fetchError ? <div className="agents-error-bar">{fetchError}</div> : null}
      {!known && value.trim() !== "" ? (
        <div className="tech-label model-select-hint">This model is not in the registry and will be saved as a custom model.</div>
      ) : null}
      {open && filtered.length > 0 ? (
        <div className="model-select-list">
          {filtered.map((m) => (
            <div
              key={m.model}
              className={`model-select-item${
                m.deprecation_date ? " model-select-item--deprecated" : ""
              }`}
              // mousedown fires before the input's blur, so the click lands.
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(m.model);
                setOpen(false);
              }}
            >
              <div className="model-select-name">
                {m.model}
                {m.deprecation_date ? (
                  <span className="model-select-deprecated">DEPRECATED {m.deprecation_date}</span>
                ) : null}
              </div>
              <div className="tech-label model-select-meta">{modelMetaLine(m)}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Centered modal frame (overlay click / esc closes). */
function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}): ReactElement {
  return (
    <div className="agents-modal-overlay" onClick={onClose}>
      <div
        className="panel agents-modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h3 className="tech-label agents-modal-title">{title}</h3>
        {children}
      </div>
    </div>
  );
}

/** Create step 1: pick a trusted prototype. */
function CreateProtoModal({
  rest,
  onPick,
  onClose,
}: {
  rest: RestClient;
  onPick: (name: string) => void;
  onClose: () => void;
}): ReactElement {
  const [protos, setProtos] = useState<ProtoRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    rest
      .listPrototypes()
      .then((list) => {
        if (cancelled) return;
        setProtos(list);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errMsg(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rest]);

  return (
    <ModalShell title="CREATE // CHOOSE PROTOTYPE" onClose={onClose}>
      {loading ? (
        <p className="agents-empty">LOADING…</p>
      ) : protos.length === 0 ? (
        <p className="agents-empty">NO PROTOTYPES AVAILABLE</p>
      ) : (
        <div className="proto-list">
          {protos.map((p) => {
            const name = String(p["name"] ?? "?");
            return (
              <div key={name} className="proto-item" onClick={() => onPick(name)}>
                <div className="proto-name">{protoLabel(p)}</div>
                {typeof p["description"] === "string" ? (
                  <div className="tech-label proto-desc">{p["description"]}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
      {error ? <div className="agents-error-bar">{error}</div> : null}
    </ModalShell>
  );
}

/** Human-facing file semantics, independent of API field names. */
function PersonaFields({ value, onChange }: {
  value: AgentPersona;
  onChange: (persona: AgentPersona) => void;
}): ReactElement {
  return <>{PERSONA_FIELDS.map(({ key, name, description }) => (
    <label className="agents-form-row" key={key}>
      <span className="tech-label">{name}</span>
      <span>{description}</span>
      <textarea
        className="input agents-form-textarea"
        value={value[key]}
        onChange={(e) => onChange({ ...value, [key]: e.target.value })}
      />
    </label>
  ))}</>;
}

/** Create step 2: name, model and optional persona file contents. */
function CreateFormModal({
  rest,
  prototype,
  onDone,
  onClose,
}: {
  rest: RestClient;
  prototype: string;
  onDone: (notice: string, result?: AgentSchedulingResponse) => void;
  onClose: () => void;
}): ReactElement {
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [persona, setPersona] = useState<AgentPersona>({ identity: "", profile: "", behavior: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    if (!name.trim()) {
      setError("NAME is required");
      return;
    }
    if (!model.trim()) {
      setError("MODEL is required (provider/model, e.g. deepseek/deepseek-chat)");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await rest.createAgent({
        prototype,
        name: name.trim(),
        model: model.trim(),
        persona,
      });
      onDone(`CREATED ${name.trim()} · ${String(result["agent_id"])}`);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`CREATE // ${prototype}`} onClose={onClose}>
      <div className="agents-form">
        <label className="agents-form-row">
          <span className="tech-label">NAME *</span>
          <input
            className="input"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="agents-form-row">
          <span className="tech-label">MODEL * (provider/model, e.g. deepseek/deepseek-chat)</span>
          <ModelSelect rest={rest} value={model} onChange={setModel} />
        </label>
        <p>Member addresses are generated by the system, independently of prototypes and social identities.</p>
        <PersonaFields value={persona} onChange={setPersona} />
      </div>
      {error ? <div className="agents-error-bar">{error}</div> : null}
      <div className="agents-modal-actions">
        <button
          type="button"
          className="btn btn--secondary"
          onClick={onClose}
          disabled={busy}
        >
          CANCEL
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "CREATING…" : "CREATE"}
        </button>
      </div>
    </ModalShell>
  );
}

/** Load all persona files, then save only the changed fields. */
function EditModal({
  rest,
  agentId,
  onDone,
  onClose,
}: {
  rest: RestClient;
  agentId: string;
  onDone: (notice: string, result?: AgentSchedulingResponse) => void;
  onClose: () => void;
}): ReactElement {
  const [persona, setPersona] = useState<AgentPersona>({ identity: "", profile: "", behavior: "" });
  const [initial, setInitial] = useState<AgentConfig | null>(null);
  const [modelName, setModelName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setInitial(null);
    setError(null);
    rest.getAgentConfig(agentId).then((config) => {
      if (cancelled) return;
      setInitial(config);
      setPersona(config.persona);
      setModelName(config.llm.model_name);
    }).catch((err: unknown) => {
      if (!cancelled) setError(errMsg(err));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [rest, agentId]);

  const submit = async (): Promise<void> => {
    setError(null);
    try {
      const update = buildAgentUpdate(initial, persona, modelName);
      if (!update.persona && !update.llm) {
        setError("No configuration changes to save");
        return;
      }
      setBusy(true);
      await rest.updateAgent(agentId, update);
      onDone(`UPDATED ${agentId}`);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`EDIT // ${agentId}`} onClose={onClose}>
      {loading ? <p>LOADING…</p> : null}
      <fieldset className="agents-form" disabled={busy || !initial}>
        <PersonaFields value={persona} onChange={setPersona} />
        <label className="agents-form-row">
          <span className="tech-label">MODEL_NAME</span>
          <ModelSelect rest={rest} value={modelName} onChange={setModelName} />
        </label>
      </fieldset>
      {error ? <div className="agents-error-bar">{error}</div> : null}
      <div className="agents-modal-actions">
        <button
          type="button"
          className="btn btn--secondary"
          onClick={onClose}
          disabled={busy}
        >
          CANCEL
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void submit()}
          disabled={busy || !initial}
        >
          {busy ? "SAVING…" : "SAVE"}
        </button>
      </div>
    </ModalShell>
  );
}

/** Delete confirmation dialog (records + home archive are removed). */
function DeleteModal({
  rest,
  agentId,
  onDone,
  onClose,
}: {
  rest: RestClient;
  agentId: string;
  onDone: (notice: string, result?: AgentSchedulingResponse) => void;
  onClose: () => void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await rest.deleteAgent(agentId);
      onDone(`REMOVED ${agentId}`);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`DELETE // ${agentId}`} onClose={onClose}>
      <p className="delete-warning">
        REMOVE MEMBER {agentId}? This deletes its records and home archive
        permanently.
      </p>
      {error ? <div className="agents-error-bar">{error}</div> : null}
      <div className="agents-modal-actions">
        <button
          type="button"
          className="btn btn--secondary"
          onClick={onClose}
          disabled={busy}
        >
          CANCEL
        </button>
        <button
          type="button"
          className="btn btn--danger"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "REMOVING…" : "CONFIRM DELETE"}
        </button>
      </div>
    </ModalShell>
  );
}

/** Permanent exit keeps mail, memory and journal; deletion is a separate action. */
function TerminateModal({ rest, agentId, onDone, onClose }: {
  rest: RestClient;
  agentId: string;
  onDone: (notice: string, result?: AgentSchedulingResponse) => void;
  onClose: () => void;
}): ReactElement {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (): Promise<void> => {
    setBusy(true);
    try {
      const result = await rest.terminateAgent(agentId);
      onDone(schedulingNotice("terminate", result), result);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };
  return <ModalShell title={`永久退出 // ${agentId}`} onClose={onClose}>
    <p>当前工作结束后永久死亡，不再响应、拒收新邮件；历史邮件和记忆保留。此操作不可恢复。</p>
    {error ? <div className="agents-error-bar">{error}</div> : null}
    <div className="agents-modal-actions">
      <button type="button" className="btn btn--secondary" disabled={busy} onClick={onClose}>取消</button>
      <button type="button" className="btn btn--danger" disabled={busy} onClick={() => void submit()}>{busy ? "正在请求…" : "确认永久退出"}</button>
    </div>
  </ModalShell>;
}


function SleepModal({ rest, agentId, onDone, onClose }: {
  rest: RestClient;
  agentId: string;
  onDone: (notice: string, result?: AgentSchedulingResponse) => void;
  onClose: () => void;
}): ReactElement {
  const [mode, setMode] = useState<"duration" | "time">("duration");
  const [minutes, setMinutes] = useState("60");
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (): Promise<void> => {
    setError(null);
    try {
      const wakeAt = sleepDeadline(mode, mode === "duration" ? minutes : time);
      setBusy(true);
      const result = await rest.sleepAgent(agentId, wakeAt);
      onDone(schedulingNotice("sleep", result), result);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };
  return <ModalShell title={`设置睡眠 // ${agentId}`} onClose={onClose}>
    <p>当前工作自然结束后入睡。期间正常收信但不处理，到期或管理员唤醒后恢复调度。</p>
    <label className="agents-form-label">设置方式
      <select className="input" value={mode} disabled={busy} onChange={(event) => setMode(event.target.value as "duration" | "time")}>
        <option value="duration">睡眠时长</option><option value="time">具体醒来时间（本地时区）</option>
      </select>
    </label>
    {mode === "duration" ? <label className="agents-form-label">睡眠分钟数
      <input className="input" type="number" min="1" step="1" value={minutes} disabled={busy} onChange={(event) => setMinutes(event.target.value)} />
      <div className="agents-modal-actions">{[15, 60, 480].map((duration) => <button key={duration} className="btn btn--secondary" type="button" disabled={busy} onClick={() => setMinutes(String(duration))}>{duration} 分钟</button>)}</div>
    </label> : <label className="agents-form-label">醒来时间（本地时区）
      <input className="input" type="datetime-local" value={time} disabled={busy} onChange={(event) => setTime(event.target.value)} />
    </label>}
    {error ? <div className="agents-error-bar">{error}</div> : null}
    <div className="agents-modal-actions">
      <button type="button" className="btn btn--secondary" disabled={busy} onClick={onClose}>取消</button>
      <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void submit()}>{busy ? "正在设置…" : "确认睡眠时间"}</button>
    </div>
  </ModalShell>;
}
