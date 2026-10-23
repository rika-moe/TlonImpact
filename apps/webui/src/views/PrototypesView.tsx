/**
 * PROTOTYPES view — prototype library management: TRUSTED LIBRARY / STAGING
 * lists, detail (metadata + parameters + behavior.tlon full text),
 * handwritten create, edit, delete, staging approve/reject.
 *
 * API contract: docs/design/webui.md「原型管理 API」. The staging approval
 * block previously in AGENTS lives here (2026-08-21 decision).
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { PrototypeError } from "../client/rest";
import type {
  ArenaLatest,
  ArenaRun,
  ArenaRunReport,
  PrototypeDetail,
  PrototypeParameter,
  PrototypePatch,
  PrototypeSummary,
  RestClient,
} from "../client/rest";
import type { ViewProps } from "../types";
import "../styles/prototypes.css";

/** Which list a prototype belongs to (drives detail/edit endpoints). */
interface Selection {
  name: string;
  staging: boolean;
}

type Modal =
  | { kind: "new" }
  | { kind: "edit"; selection: Selection; detail: PrototypeDetail }
  | { kind: "delete"; name: string };

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Error text for prototype writes: surface reason + message on 400. */
function writeErrMsg(err: unknown): string {
  if (err instanceof PrototypeError && err.reason) {
    return `${err.reason}: ${err.message}`;
  }
  return errMsg(err);
}

/** Arena run terminal statuses (the runner's TERMINAL_STATUSES). */
const ARENA_TERMINAL = new Set(["done", "timed_out", "failed"]);

/** Poll interval for an in-flight arena run. */
const ARENA_POLL_MS = 4000;

export function PrototypesView({ rest }: ViewProps): ReactElement {
  const [trusted, setTrusted] = useState<PrototypeSummary[]>([]);
  const [staging, setStaging] = useState<PrototypeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [detail, setDetail] = useState<PrototypeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [trustedList, stagingList] = await Promise.all([
        rest.listPrototypes(),
        rest.listStaging(),
      ]);
      setTrusted(trustedList);
      setStaging(stagingList);
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

  const fetchDetail = useCallback(
    async (sel: Selection): Promise<void> => {
      setDetail(null);
      setDetailLoading(true);
      try {
        setDetail(
          sel.staging
            ? await rest.getStagingPrototype(sel.name)
            : await rest.getPrototype(sel.name),
        );
      } catch (err) {
        setError(errMsg(err));
      } finally {
        setDetailLoading(false);
      }
    },
    [rest],
  );

  /** Toggle the detail panel; fetches the detail endpoint on expand. */
  const selectProto = (sel: Selection): void => {
    if (selected && selected.name === sel.name && selected.staging === sel.staging) {
      setSelected(null);
      setDetail(null);
      return;
    }
    setSelected(sel);
    void fetchDetail(sel);
  };

  /** Stable detail refetch for the arena panel (post-run refresh). */
  const refreshDetail = useCallback(() => {
    if (selected) void fetchDetail(selected);
  }, [selected, fetchDetail]);

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
      // The entry left staging; drop its detail panel.
      if (selected?.staging && selected.name === name) {
        setSelected(null);
        setDetail(null);
      }
      await load();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  /** Shared post-action wrap-up: close modal, notice, reload both lists. */
  const onActionDone = (msg: string): void => {
    setModal(null);
    setNotice(msg);
    setError(null);
    void load();
    if (selected) void fetchDetail(selected);
  };

  return (
    <div className="prototypes-view">
      <div className="prototypes-toolbar">
        <h2 className="tech-label prototypes-title">PROTOTYPES</h2>
        <span className="tech-label">
          {trusted.length} TRUSTED / {staging.length} STAGING
        </span>
        <span className="prototypes-spacer" />
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
          onClick={() => setModal({ kind: "new" })}
        >
          + NEW
        </button>
      </div>

      {error ? <div className="prototypes-error-bar">{error}</div> : null}
      {notice ? <div className="prototypes-notice">{notice}</div> : null}

      {loading ? (
        <p className="prototypes-empty">LOADING…</p>
      ) : (
        <div className="prototypes-layout">
          <div className="prototypes-body">
            <div className="prototypes-list">
              <section className="proto-section">
                <h3 className="tech-label proto-section-title">
                  TRUSTED LIBRARY ({trusted.length})
                </h3>
                {trusted.length === 0 ? (
                  <p className="prototypes-empty">NO TRUSTED PROTOTYPES</p>
                ) : (
                  trusted.map((p) => (
                    <ProtoCard
                      key={p.name}
                      proto={p}
                      staging={false}
                      selected={
                        selected !== null && !selected.staging && selected.name === p.name
                      }
                      onSelect={() => selectProto({ name: p.name, staging: false })}
                    />
                  ))
                )}
              </section>
              {staging.length > 0 ? (
                <section className="proto-section">
                  <h3 className="tech-label proto-section-title proto-section-title--staging">
                    STAGING ({staging.length})
                  </h3>
                  {staging.map((p) => (
                    <ProtoCard
                      key={p.name}
                      proto={p}
                      staging={true}
                      selected={
                        selected !== null && selected.staging && selected.name === p.name
                      }
                      onSelect={() => selectProto({ name: p.name, staging: true })}
                    />
                  ))}
                </section>
              ) : null}
            </div>

            {selected ? (
              <section className="panel proto-detail">
                <div className="proto-detail-head">
                  <span className="tech-label">
                    {selected.staging ? "STAGING" : "TRUSTED"} // {detail?.display_name || selected.name}
                  </span>
                  <span className="prototypes-spacer" />
                  <button
                    type="button"
                    className="btn btn--secondary"
                    disabled={busy || detail === null}
                    onClick={() => {
                      if (detail) setModal({ kind: "edit", selection: selected, detail });
                    }}
                  >
                    EDIT
                  </button>
                  {selected.staging ? (
                    <>
                      <button
                        type="button"
                        className="btn btn--primary"
                        disabled={busy}
                        onClick={() => void approve(selected.name, true)}
                      >
                        APPROVE
                      </button>
                      <button
                        type="button"
                        className="btn btn--danger"
                        disabled={busy}
                        onClick={() => void approve(selected.name, false)}
                      >
                        REJECT
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--danger"
                      disabled={busy}
                      onClick={() => setModal({ kind: "delete", name: selected.name })}
                    >
                      DELETE
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => {
                      setSelected(null);
                      setDetail(null);
                    }}
                  >
                    CLOSE
                  </button>
                </div>
                {detailLoading ? (
                  <p className="prototypes-empty">LOADING…</p>
                ) : detail ? (
                  <>
                    <div className="proto-meta">
                      <div className="proto-meta-row">
                        <span className="tech-label">ID</span>
                        <span className="proto-meta-value">{detail.name}</span>
                      </div>
                      <div className="proto-meta-row">
                        <span className="tech-label">BEHAVIOR DISPLAY NAME</span>
                        <span className="proto-meta-value">
                          {detail.display_name || "—"}
                        </span>
                      </div>
                      <div className="proto-meta-row">
                        <span className="tech-label">AUTHOR</span>
                        <span className="proto-meta-value">{detail.author || "unknown"}</span>
                      </div>
                      <div className="proto-meta-row">
                        <span className="tech-label">TRUSTED</span>
                        <span className="proto-meta-value">{String(detail.trusted)}</span>
                      </div>
                      {detail.description ? (
                        <div className="proto-meta-row">
                          <span className="tech-label">DESCRIPTION</span>
                          <span className="proto-meta-value">{detail.description}</span>
                        </div>
                      ) : null}
                    </div>
                    <span className="tech-label">PARAMETERS</span>
                    {detail.parameters.length > 0 ? (
                      <ProtoParams parameters={detail.parameters} />
                    ) : (
                      <p className="prototypes-empty">NO PARAMETERS</p>
                    )}
                    <span className="tech-label">behavior.tlon</span>
                    {detail.red_flags.length > 0 ? (
                      <div className="proto-redflags">
                        {detail.red_flags.map((flag) => (
                          <span className="proto-redflag" key={flag}>
                            RED FLAG // {flag}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <pre className="terminal proto-behavior">{detail.behavior}</pre>
                    {Object.entries(detail.modules).map(([path, source]) => (
                      <div key={path}>
                        <span className="tech-label">modules/{path}</span>
                        <pre className="terminal proto-behavior">{source}</pre>
                      </div>
                    ))}
                    {detail.lineage?.map((entry) => (
                      <div key={entry.name}>
                        <span className="tech-label">GENERATION {entry.generation} // {entry.name}</span>
                        {entry.proposal_note ? <p>{entry.proposal_note}</p> : null}
                        {entry.broken ? <p>Parent source unavailable; lineage is incomplete.</p> : null}
                        {entry.diff_from_parent ? <pre className="terminal proto-behavior">{entry.diff_from_parent}</pre> : null}
                      </div>
                    ))}
                    {selected.staging ? (
                      <ArenaPanel
                        rest={rest}
                        protoName={selected.name}
                        arena={detail.arena ?? null}
                        onRefreshDetail={refreshDetail}
                      />
                    ) : null}
                  </>
                ) : (
                  <p className="prototypes-empty">NO DETAIL</p>
                )}
              </section>
            ) : (
              <section className="panel proto-detail proto-detail--empty">
                <p className="prototypes-empty">SELECT A PROTOTYPE ON THE LEFT TO VIEW DETAILS</p>
              </section>
            )}
          </div>
        </div>
      )}

      {modal?.kind === "new" ? (
        <NewModal rest={rest} onDone={onActionDone} onClose={() => setModal(null)} />
      ) : null}
      {modal?.kind === "edit" ? (
        <EditModal
          rest={rest}
          selection={modal.selection}
          detail={modal.detail}
          onDone={onActionDone}
          onClose={() => setModal(null)}
        />
      ) : null}
      {modal?.kind === "delete" ? (
        <DeleteModal
          rest={rest}
          name={modal.name}
          onDone={(msg) => {
            if (selected?.name === modal.name && !selected.staging) {
              setSelected(null);
              setDetail(null);
            }
            onActionDone(msg);
          }}
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

/** One prototype list card (cyan for trusted, yellow outline for staging). */
function ProtoCard({
  proto,
  staging,
  selected,
  onSelect,
}: {
  proto: PrototypeSummary;
  staging: boolean;
  selected: boolean;
  onSelect: () => void;
}): ReactElement {
  return (
    <div
      className={`proto-card${staging ? " proto-card--staging" : ""}${
        selected ? " proto-card--selected" : ""
      }`}
      onClick={onSelect}
    >
      <div className="proto-card-head">
        <div className="proto-card-display">{proto.display_name || proto.name}</div>
        <span
          className={`proto-card-badge${staging ? " proto-card-badge--staging" : ""}`}
        >
          {staging ? "STAGING" : "TRUSTED"}
        </span>
      </div>
      <div className="proto-card-name">{proto.name}</div>
      {proto.description ? (
        <div className="proto-card-desc">{proto.description}</div>
      ) : null}
      {proto.parameters.length > 0 ? (
        <ProtoParams parameters={proto.parameters} />
      ) : null}
      <div className="tech-label">AUTHOR: {proto.author || "unknown"}</div>
    </div>
  );
}

/**
 * Parameter chips: `name: type` pills, `*` marks required.
 * Shared by the list cards and the detail panel (docs/design/webui.md).
 */
function ProtoParams({
  parameters,
}: {
  parameters: PrototypeParameter[];
}): ReactElement {
  return (
    <div className="proto-params">
      {parameters.map((param) => (
        <span className="proto-param" key={param.name}>
          {param.name}: {param.type}
          {param.required ? " *" : ""}
        </span>
      ))}
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
    <div className="proto-modal-overlay" onClick={onClose}>
      <div
        className="panel proto-modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h3 className="tech-label proto-modal-title">{title}</h3>
        {children}
      </div>
    </div>
  );
}

/** Handwritten creation: behavior label + metadata + behavior.tlon full text. */
function NewModal({
  rest,
  onDone,
  onClose,
}: {
  rest: RestClient;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [behavior, setBehavior] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    if (!displayName.trim()) {
      setError("Behavior display name is required");
      return;
    }
    if (!behavior.trim()) {
      setError("BEHAVIOR is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await rest.createPrototype({
        behavior,
        display_name: displayName.trim(),
        description: description.trim() || undefined,
      });
      const created = result["prototype"] as { name: string };
      onDone(`CREATED ${displayName.trim()} · ${created.name}`);
    } catch (err) {
      setError(writeErrMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title="NEW // HANDWRITTEN PROTOTYPE" onClose={onClose}>
      <div className="proto-form">
        <p>Technical IDs are generated by the system. Display names describe how a prototype acts; they do not assign a profession or role.</p>
        <label className="proto-form-row">
          <span className="tech-label">BEHAVIOR DISPLAY NAME * (e.g. extended-budget tool iteration)</span>
          <input
            className="input"
            value={displayName}
            autoFocus
            placeholder="Describe the process, context, or budget"
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </label>
        <label className="proto-form-row">
          <span className="tech-label">DESCRIPTION (optional)</span>
          <input
            className="input"
            value={description}
            placeholder="Describe triggers and behavior, rather than using a job title"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="proto-form-row">
          <span className="tech-label">behavior.tlon *</span>
          <textarea
            className="input proto-form-behavior"
            value={behavior}
            spellCheck={false}
            onChange={(e) => setBehavior(e.target.value)}
          />
        </label>
      </div>
      {error ? <div className="prototypes-error-bar">{error}</div> : null}
      <div className="proto-modal-actions">
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

/** Edit metadata and/or behavior; PATCH sends only changed fields. */
function EditModal({
  rest,
  selection,
  detail,
  onDone,
  onClose,
}: {
  rest: RestClient;
  selection: Selection;
  detail: PrototypeDetail;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [displayName, setDisplayName] = useState(detail.display_name);
  const [description, setDescription] = useState(detail.description);
  const [behavior, setBehavior] = useState(detail.behavior);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    const patch: PrototypePatch = {};
    if (displayName !== detail.display_name) patch.display_name = displayName;
    if (description !== detail.description) patch.description = description;
    if (behavior !== detail.behavior) patch.behavior = behavior;
    if (Object.keys(patch).length === 0) {
      setError("No changes to save");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (selection.staging) {
        await rest.updateStagingPrototype(selection.name, patch);
      } else {
        await rest.updatePrototype(selection.name, patch);
      }
      onDone(`UPDATED ${selection.name}`);
    } catch (err) {
      setError(writeErrMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`EDIT // ${selection.name}`} onClose={onClose}>
      <div className="proto-form">
        <label className="proto-form-row">
          <span className="tech-label">BEHAVIOR DISPLAY NAME</span>
          <input
            className="input"
            value={displayName}
            autoFocus
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </label>
        <label className="proto-form-row">
          <span className="tech-label">DESCRIPTION</span>
          <input
            className="input"
            value={description}
            placeholder="Describe triggers and behavior, rather than using a job title"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="proto-form-row">
          {Object.keys(detail.modules).length > 0 ? (
            <p>Module program sources are immutable. Create a new prototype to change the program.</p>
          ) : null}
          <span className="tech-label">behavior.tlon</span>
          <textarea
            className="input proto-form-behavior"
            value={behavior}
            readOnly={Object.keys(detail.modules).length > 0}
            spellCheck={false}
            onChange={(e) => setBehavior(e.target.value)}
          />
        </label>
      </div>
      {error ? <div className="prototypes-error-bar">{error}</div> : null}
      <div className="proto-modal-actions">
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

/** Delete confirmation dialog (trusted library only; staging uses reject). */
function DeleteModal({
  rest,
  name,
  onDone,
  onClose,
}: {
  rest: RestClient;
  name: string;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await rest.deletePrototype(name);
      onDone(`DELETED ${name}`);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`DELETE // ${name}`} onClose={onClose}>
      <p className="proto-delete-warning">
        DELETE PROTOTYPE {name}? Members already created from it are not
        affected, but the prototype is removed permanently.
      </p>
      {error ? <div className="prototypes-error-bar">{error}</div> : null}
      <div className="proto-modal-actions">
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
          {busy ? "DELETING…" : "CONFIRM DELETE"}
        </button>
      </div>
    </ModalShell>
  );
}

/**
 * 演武场面板（staging 详情专属，docs/design/arena.md）：触发一次 run、
 * 轮询到终态、展示最新报告（ARENA.md 原文）与报告红旗。报告是证据不是
 * 判决——面板只呈现，不接 approve 逻辑。
 */
function ArenaPanel({
  rest,
  protoName,
  arena,
  onRefreshDetail,
}: {
  rest: RestClient;
  protoName: string;
  arena: ArenaLatest | null;
  onRefreshDetail: () => void;
}): ReactElement {
  const [model, setModel] = useState("");
  const [activeRun, setActiveRun] = useState<ArenaRun | null>(null);
  const [report, setReport] = useState<ArenaRunReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const running = activeRun !== null && !ARENA_TERMINAL.has(activeRun.status);

  // Pick up an in-flight run for this prototype (triggered elsewhere, or
  // the panel remounted mid-run).
  useEffect(() => {
    setActiveRun(null);
    let cancelled = false;
    rest
      .listArenaRuns()
      .then((runs) => {
        if (cancelled) return;
        const active = runs.find(
          (r) => r.prototype === protoName && !ARENA_TERMINAL.has(r.status),
        );
        if (active) setActiveRun(active);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [rest, protoName]);

  // Poll the active run; on a terminal status refresh the detail (arena
  // field) — the report effect below then loads the fresh ARENA.md.
  useEffect(() => {
    if (!activeRun || ARENA_TERMINAL.has(activeRun.status)) return;
    const timer = setInterval(() => {
      rest
        .getArenaRun(activeRun.run_id)
        .then((run) => {
          setActiveRun(run);
          if (ARENA_TERMINAL.has(run.status)) onRefreshDetail();
        })
        .catch(() => undefined);
    }, ARENA_POLL_MS);
    return () => clearInterval(timer);
  }, [rest, activeRun, onRefreshDetail]);

  // The run whose report is shown: the just-finished active run wins, else
  // the latest archived run carried by the detail payload.
  const reportRunId =
    activeRun && ARENA_TERMINAL.has(activeRun.status)
      ? activeRun.run_id
      : (arena?.latest_run?.run_id ?? null);
  useEffect(() => {
    setReport(null);
    if (!reportRunId) return;
    let cancelled = false;
    rest
      .getArenaReport(protoName, reportRunId)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [rest, protoName, reportRunId]);

  const start = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const run = await rest.startArenaRun(
        protoName,
        model.trim() ? { model: model.trim() } : {},
      );
      setActiveRun(run);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const flags = arena?.red_flags ?? [];
  return (
    <section className="proto-arena">
      <span className="tech-label">ARENA</span>
      <div className="proto-arena-controls">
        <input
          className="input proto-arena-model"
          placeholder="Leave blank to use TLON_ARENA_MODEL"
          value={model}
          spellCheck={false}
          disabled={busy || running}
          onChange={(e) => setModel(e.target.value)}
        />
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || running}
          onClick={() => void start()}
        >
          {running ? "RUNNING…" : busy ? "STARTING…" : "START ARENA RUN"}
        </button>
      </div>
      {activeRun ? (
        <div className="tech-label proto-arena-status">
          RUN {activeRun.run_id} // {activeRun.status.toUpperCase()}
          {activeRun.fed_count > 0
            ? ` // SESSION ${activeRun.completed_count}/${activeRun.fed_count}`
            : ""}
          {activeRun.error ? ` // ${activeRun.error}` : ""}
        </div>
      ) : null}
      {error ? <div className="prototypes-error-bar">{error}</div> : null}
      {flags.length > 0 ? (
        <div className="proto-redflags">
          {flags.map((flag, index) => (
            <span
              className="proto-redflag"
              key={`${flag.kind}-${index}`}
              title={flag.evidence}
            >
              ARENA // {flag.kind}
            </span>
          ))}
        </div>
      ) : null}
      {report ? (
        <pre className="terminal proto-behavior proto-arena-report">
          {report.arena_md}
        </pre>
      ) : null}
    </section>
  );
}
