/**
 * AGENTS view — member card grid (state-colored, with lineage) +
 * structured detail panel (with the member's recent logs embedded,
 * GET /agents/{id}/logs), create (pick a prototype -> form), edit,
 * delete (confirm).
 *
 * Cards carry an event-driven live status line (lib/agentLiveStatus.ts):
 * THINKING badge while agent.thinking_start is open, last LLM call
 * duration + model, most recent tool call. Structural changes
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
import type { AgentDetail, AgentSummary, LogEntry, RestClient } from "../client/rest";
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
  | { kind: "delete"; agentId: string };

type StateTone = "cyan" | "dim" | "magenta" | "yellow";

/** Card/badge color from a state value. */
function stateTone(state: string): StateTone {
  const s = state.toLowerCase();
  if (["running", "active", "idle", "online"].includes(s)) return "cyan";
  if (["error", "failed"].includes(s)) return "magenta";
  if (["sleeping", "stopped", "offline"].includes(s)) return "dim";
  return "yellow";
}

function protoLabel(p: ProtoRecord): string {
  const name = String(p["name"] ?? "?");
  const display = String(p["display_name"] ?? "");
  return display && display !== name ? `${name} — ${display}` : name;
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
  // also feeds the per-agent live status reducer (thinking/LLM/tool).
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
  const onActionDone = (msg: string): void => {
    setModal(null);
    setNotice(msg);
    setError(null);
    void load();
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
        <p className="agents-empty">NO AGENTS // 成员列表为空</p>
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
                  <span className={`state-badge state-badge--${stateTone(agent.state)}`}>
                    {agent.state}
                  </span>
                </div>
                {agent.name !== id ? (
                  <div className="tech-label agent-card-name">{agent.name}</div>
                ) : null}
                <div className="tech-label agent-card-meta">
                  {agent.prototype ?? "MANUAL"} · GEN {agent.spawn_depth}
                </div>
                {live[id] ? <AgentLiveLine status={live[id]} /> : null}
                <div className="agent-card-actions">
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

/**
 * Event-driven live status line on a member card (lib/agentLiveStatus.ts):
 * THINKING badge while a thinking round is open, last LLM call duration
 * + model, and the most recent tool call (✓/✗). Renders nothing until
 * the first relevant event for this agent arrives.
 */
function AgentLiveLine({ status }: { status: AgentLiveStatus }): ReactElement | null {
  const llmMs = status.lastLlmMs;
  if (!status.thinking && llmMs === undefined && status.lastTool === undefined) return null;
  return (
    <div className="tech-label agent-card-live">
      {status.thinking ? <span className="live-badge">THINKING</span> : null}
      {llmMs !== undefined ? (
        <span className="live-item">
          LLM {formatDurationMs(llmMs)}
          {status.lastLlmModel ? ` · ${status.lastLlmModel}` : ""}
        </span>
      ) : null}
      {status.lastTool ? (
        <span
          className={`live-item ${status.lastTool.success ? "live-tool--ok" : "live-tool--fail"}`}
        >
          {status.lastTool.name} {status.lastTool.success ? "✓" : "✗"}
        </span>
      ) : null}
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
        <span className={`state-badge state-badge--${stateTone(detail.state)}`}>
          {detail.state}
        </span>
      </DetailRow>
      <DetailRow label="NAME">{detail.name}</DetailRow>
      <DetailRow label="PROTOTYPE">{prototype}</DetailRow>
      <DetailRow label="SPAWNED_BY">{spawnedBy ?? "ROOT // 初代"}</DetailRow>
      <DetailRow label="SPAWN_DEPTH">
        {spawnDepth !== undefined ? `GEN ${spawnDepth}` : "—"}
      </DetailRow>
      {configEntries.length > 0 ? (
        <div className="agent-detail-config">
          <span className="tech-label agent-detail-label">CONFIG</span>
          {configEntries.map(([key, value]) => (
            <DetailRow key={key} label={key.toUpperCase()}>
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
        <p className="agents-empty">NO PROTOTYPES // 无可用原型</p>
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

/** Create step 2: name / agent_id? / identity? -> POST /admin/config/agents. */
function CreateFormModal({
  rest,
  prototype,
  onDone,
  onClose,
}: {
  rest: RestClient;
  prototype: string;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [name, setName] = useState("");
  const [agentIdField, setAgentIdField] = useState("");
  const [identity, setIdentity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    if (!name.trim()) {
      setError("NAME 不能为空");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await rest.createAgent({
        prototype,
        name: name.trim(),
        agent_id: agentIdField.trim() || undefined,
        persona: identity.trim() ? { identity: identity.trim() } : undefined,
      });
      onDone(`CREATED ${name.trim()}`);
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
          <span className="tech-label">AGENT_ID (可选)</span>
          <input
            className="input"
            value={agentIdField}
            onChange={(e) => setAgentIdField(e.target.value)}
          />
        </label>
        <label className="agents-form-row">
          <span className="tech-label">IDENTITY (可选)</span>
          <textarea
            className="input agents-form-textarea"
            value={identity}
            onChange={(e) => setIdentity(e.target.value)}
          />
        </label>
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

/** Edit persona.identity / llm.model_name / llm.temperature (PATCH). */
function EditModal({
  rest,
  agentId,
  onDone,
  onClose,
}: {
  rest: RestClient;
  agentId: string;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [identity, setIdentity] = useState("");
  const [modelName, setModelName] = useState("");
  const [temperature, setTemperature] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    const identityTrimmed = identity.trim();
    const modelTrimmed = modelName.trim();
    const tempTrimmed = temperature.trim();
    if (!identityTrimmed && !modelTrimmed && !tempTrimmed) {
      setError("未填写任何修改项");
      return;
    }
    let tempValue: number | undefined;
    if (tempTrimmed) {
      tempValue = Number(tempTrimmed);
      if (Number.isNaN(tempValue)) {
        setError("TEMPERATURE 必须是数字");
        return;
      }
    }
    const llm: { model_name?: string; temperature?: number } = {};
    if (modelTrimmed) llm.model_name = modelTrimmed;
    if (tempValue !== undefined) llm.temperature = tempValue;

    setBusy(true);
    setError(null);
    try {
      await rest.updateAgent(agentId, {
        persona: identityTrimmed ? { identity: identityTrimmed } : undefined,
        llm: Object.keys(llm).length > 0 ? llm : undefined,
      });
      onDone(`UPDATED ${agentId}`);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`EDIT // ${agentId}`} onClose={onClose}>
      <div className="agents-form">
        <label className="agents-form-row">
          <span className="tech-label">IDENTITY (留空不改)</span>
          <textarea
            className="input agents-form-textarea"
            value={identity}
            autoFocus
            onChange={(e) => setIdentity(e.target.value)}
          />
        </label>
        <label className="agents-form-row">
          <span className="tech-label">MODEL_NAME (留空不改)</span>
          <input
            className="input"
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          />
        </label>
        <label className="agents-form-row">
          <span className="tech-label">TEMPERATURE (留空不改)</span>
          <input
            className="input"
            value={temperature}
            inputMode="decimal"
            onChange={(e) => setTemperature(e.target.value)}
          />
        </label>
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
  onDone: (notice: string) => void;
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
