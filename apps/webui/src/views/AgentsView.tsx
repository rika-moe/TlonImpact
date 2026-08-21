/**
 * AGENTS view — member card grid (state-colored) + detail terminal, create
 * (pick a prototype -> form), edit, delete (confirm), staging prototype
 * approval.
 *
 * Ported from apps/operator/src/views/AgentsView.tsx: same REST/SSE semantics
 * (GET /agents[/{id}], /admin/config/agents CRUD, /admin/prototypes(/staging/*),
 * SSE agents snapshot + agent* events), rendering layer rewritten to DOM.
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import type { RestClient } from "../client/rest";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/agents.css";

type AgentRecord = Record<string, unknown>;
type ProtoRecord = Record<string, unknown>;

type Modal =
  | { kind: "create-proto" }
  | { kind: "create-form"; prototype: string }
  | { kind: "edit"; agentId: string }
  | { kind: "delete"; agentId: string };

type StateTone = "cyan" | "dim" | "magenta" | "yellow";

/** Card/badge color from the member's state value. */
function stateTone(agent: AgentRecord): StateTone {
  const s = String(agent["state"] ?? "").toLowerCase();
  if (["running", "active", "idle", "online"].includes(s)) return "cyan";
  if (["error", "failed"].includes(s)) return "magenta";
  if (["sleeping", "stopped", "offline"].includes(s)) return "dim";
  return "yellow";
}

function agentIdOf(agent: AgentRecord): string {
  return String(agent["id"] ?? agent["name"] ?? "?");
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
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [staging, setStaging] = useState<ProtoRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [busy, setBusy] = useState(false);

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

  const loadStaging = useCallback(async () => {
    try {
      setStaging(await rest.listStaging());
    } catch (err) {
      setError(errMsg(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
    void loadStaging();
  }, [load, loadStaging]);

  // Real-time refresh: the session's agents snapshot replaces the list;
  // any agent* event triggers a reload.
  useEffect(() => {
    const onAgents = (list: unknown[]): void => {
      setAgents(list as AgentRecord[]);
      setError(null);
    };
    const onEvent = (ev: ServerEvent): void => {
      if (ev.eventType.startsWith("agent")) void load();
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

  const approve = async (name: string, accept: boolean): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      if (accept) {
        await rest.approvePrototype(name);
        setNotice(`APPROVED ${name}`);
      } else {
        await rest.rejectPrototype(name);
        setNotice(`REJECTED ${name}`);
      }
      await loadStaging();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  /** Shared post-action wrap-up: close modal, notice, reload. */
  const onActionDone = (msg: string): void => {
    setModal(null);
    setNotice(msg);
    setError(null);
    void load();
    void loadStaging();
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
            void loadStaging();
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

      {staging.length > 0 ? (
        <section className="panel staging-section">
          <h3 className="tech-label staging-title">
            STAGING // 待审批原型 ({staging.length})
          </h3>
          {staging.map((p) => {
            const name = String(p["name"] ?? "?");
            return (
              <div className="staging-item" key={name}>
                <span className="staging-name">{protoLabel(p)}</span>
                <span className="tech-label">
                  AUTHOR: {String(p["author"] ?? "unknown")}
                </span>
                <span className="agents-spacer" />
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy}
                  onClick={() => void approve(name, true)}
                >
                  APPROVE
                </button>
                <button
                  type="button"
                  className="btn btn--danger"
                  disabled={busy}
                  onClick={() => void approve(name, false)}
                >
                  REJECT
                </button>
              </div>
            );
          })}
        </section>
      ) : null}

      {loading ? (
        <p className="agents-empty">LOADING…</p>
      ) : agents.length === 0 ? (
        <p className="agents-empty">NO AGENTS // 成员列表为空</p>
      ) : (
        <div className="agents-grid">
          {agents.map((agent) => {
            const id = agentIdOf(agent);
            const state = String(agent["state"] ?? "unknown");
            const name =
              typeof agent["name"] === "string" && agent["name"] !== id
                ? agent["name"]
                : null;
            return (
              <div
                key={id}
                className={`agent-card${selectedId === id ? " agent-card--selected" : ""}`}
                onClick={() => selectAgent(id)}
              >
                <div className="agent-card-head">
                  <span className="agent-card-id">{id}</span>
                  <span className={`state-badge state-badge--${stateTone(agent)}`}>
                    {state}
                  </span>
                </div>
                {name ? <div className="tech-label agent-card-name">{name}</div> : null}
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
            <pre className="terminal agent-detail-json">
              {JSON.stringify(detail, null, 2)}
            </pre>
          ) : (
            <p className="agents-empty">NO DETAIL</p>
          )}
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
