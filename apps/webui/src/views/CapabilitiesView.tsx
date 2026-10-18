/**
 * CAPABILITIES view — capability library management (docs/design/
 * capabilities.md, issue #45): TRUSTED LIBRARY / STAGING lists, detail
 * (metadata + capability.tlon full text + REVIEW.md records), staging
 * approve/reject, and the human-authored proposal form (v1 has no
 * agent-side authoring tool — this form is the main entry).
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { CapabilityError } from "../client/rest";
import type { CapabilityDetail, CapabilitySummary, RestClient } from "../client/rest";
import type { ViewProps } from "../types";
import "../styles/capabilities.css";

/** Which list a capability belongs to (drives detail endpoints). */
interface Selection {
  name: string;
  staging: boolean;
}

type Modal = { kind: "new" } | { kind: "reject"; name: string };

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Error text for staging writes: surface reason + message on 400. */
function writeErrMsg(err: unknown): string {
  if (err instanceof CapabilityError && err.reason) {
    return `${err.reason}: ${err.message}`;
  }
  return errMsg(err);
}

/** Purity marker of one word (null = artifact failed fail-soft parsing). */
function pureLabel(pure: boolean | null): string {
  if (pure === null) return "PURE ?";
  return pure ? "PURE" : "IMPURE";
}

export function CapabilitiesView({ rest }: ViewProps): ReactElement {
  const [trusted, setTrusted] = useState<CapabilitySummary[]>([]);
  const [staging, setStaging] = useState<CapabilitySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [detail, setDetail] = useState<CapabilityDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [trustedList, stagingList] = await Promise.all([
        rest.listCapabilities(),
        rest.listStagingCapabilities(),
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
            ? await rest.getStagingCapability(sel.name)
            : await rest.getCapability(sel.name),
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
  const selectCap = (sel: Selection): void => {
    if (selected && selected.name === sel.name && selected.staging === sel.staging) {
      setSelected(null);
      setDetail(null);
      return;
    }
    setSelected(sel);
    void fetchDetail(sel);
  };

  const approve = async (name: string): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await rest.approveCapability(name);
      setNotice(`APPROVED ${name} // AVAILABLE ON NEXT MEMBER WAKE`);
      // The entry left staging; drop its detail panel.
      if (selected?.staging && selected.name === name) {
        setSelected(null);
        setDetail(null);
      }
      await load();
    } catch (err) {
      setError(writeErrMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="capabilities-view">
      <div className="capabilities-toolbar">
        <h2 className="tech-label capabilities-title">CAPABILITIES</h2>
        <span className="tech-label">
          {trusted.length} TRUSTED / {staging.length} STAGING
        </span>
        <span className="capabilities-spacer" />
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
          + NEW PROPOSAL
        </button>
      </div>

      {error ? <div className="capabilities-error-bar">{error}</div> : null}
      {notice ? <div className="capabilities-notice">{notice}</div> : null}

      {loading ? (
        <p className="capabilities-empty">LOADING…</p>
      ) : (
        <div className="capabilities-body">
          <div className="capabilities-list">
            <section className="cap-section">
              <h3 className="tech-label cap-section-title">
                TRUSTED LIBRARY ({trusted.length})
              </h3>
              {trusted.length === 0 ? (
                <p className="capabilities-empty">NO TRUSTED CAPABILITIES</p>
              ) : (
                trusted.map((c) => (
                  <CapCard
                    key={c.name}
                    cap={c}
                    staging={false}
                    selected={
                      selected !== null && !selected.staging && selected.name === c.name
                    }
                    onSelect={() => selectCap({ name: c.name, staging: false })}
                  />
                ))
              )}
            </section>
            {staging.length > 0 ? (
              <section className="cap-section">
                <h3 className="tech-label cap-section-title cap-section-title--staging">
                  STAGING ({staging.length})
                </h3>
                {staging.map((c) => (
                  <CapCard
                    key={c.name}
                    cap={c}
                    staging={true}
                    selected={
                      selected !== null && selected.staging && selected.name === c.name
                    }
                    onSelect={() => selectCap({ name: c.name, staging: true })}
                  />
                ))}
              </section>
            ) : null}
          </div>

          {selected ? (
            <section className="panel cap-detail">
              <div className="cap-detail-head">
                <span className="tech-label">
                  {selected.staging ? "STAGING" : "TRUSTED"} // {selected.name}
                </span>
                <span className="capabilities-spacer" />
                {selected.staging ? (
                  <>
                    <button
                      type="button"
                      className="btn btn--primary"
                      disabled={busy}
                      onClick={() => void approve(selected.name)}
                    >
                      APPROVE
                    </button>
                    <button
                      type="button"
                      className="btn btn--danger"
                      disabled={busy}
                      onClick={() => setModal({ kind: "reject", name: selected.name })}
                    >
                      REJECT
                    </button>
                  </>
                ) : null}
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
                <p className="capabilities-empty">LOADING…</p>
              ) : detail ? (
                <>
                  <div className="cap-meta">
                    <div className="cap-meta-row">
                      <span className="tech-label">NAME</span>
                      <span className="cap-meta-value">{detail.name}</span>
                    </div>
                    <div className="cap-meta-row">
                      <span className="tech-label">PURITY</span>
                      <span
                        className={`cap-purity${
                          detail.pure === false ? " cap-purity--impure" : ""
                        }`}
                      >
                        {pureLabel(detail.pure)}
                      </span>
                    </div>
                    <div className="cap-meta-row">
                      <span className="tech-label">AUTHOR</span>
                      <span className="cap-meta-value">{detail.author || "unknown"}</span>
                    </div>
                    <div className="cap-meta-row">
                      <span className="tech-label">TRUSTED</span>
                      <span className="cap-meta-value">{String(detail.trusted)}</span>
                    </div>
                    {detail.deprecated ? (
                      <div className="cap-meta-row">
                        <span className="tech-label">DEPRECATED</span>
                        <span className="cap-deprecated-badge">DEPRECATED</span>
                      </div>
                    ) : null}
                    {detail.description ? (
                      <div className="cap-meta-row">
                        <span className="tech-label">DESCRIPTION</span>
                        <span className="cap-meta-value">{detail.description}</span>
                      </div>
                    ) : null}
                    {detail.proposal_note ? (
                      <div className="cap-meta-row">
                        <span className="tech-label">PROPOSAL_NOTE</span>
                        <span className="cap-meta-value">{detail.proposal_note}</span>
                      </div>
                    ) : null}
                  </div>
                  <span className="tech-label">capability.tlon</span>
                  <pre className="terminal cap-artifact">{detail.artifact}</pre>
                  {detail.examples && detail.examples.length > 0 ? (
                    <>
                      <span className="tech-label">EXAMPLES ({detail.examples.length})</span>
                      <div className="cap-examples">
                        {detail.examples.map((ex, i) => (
                          <div key={i} className="cap-example">
                            <div className="cap-example-head">
                              <span className="tech-label">#{i}</span>
                              <span
                                className={`cap-example-badge${
                                  ex.passed ? "" : " cap-example-badge--fail"
                                }`}
                              >
                                {ex.passed ? "PASS" : "FAIL"}
                              </span>
                            </div>
                            <div className="cap-meta-row">
                              <span className="tech-label">ARGS</span>
                              <span className="cap-meta-value">{ex.args}</span>
                            </div>
                            <div className="cap-meta-row">
                              <span className="tech-label">EXPECT</span>
                              <span className="cap-meta-value">{ex.expect}</span>
                            </div>
                            {ex.error !== undefined ? (
                              <div className="cap-meta-row">
                                <span className="tech-label">ERROR</span>
                                <span className="cap-meta-value cap-example-error">
                                  {ex.error}
                                </span>
                              </div>
                            ) : (
                              <div className="cap-meta-row">
                                <span className="tech-label">ACTUAL</span>
                                <span className="cap-meta-value">{ex.actual}</span>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {detail.reviews ? (
                    <>
                      <span className="tech-label">REVIEW.md</span>
                      <pre className="terminal cap-artifact">{detail.reviews}</pre>
                    </>
                  ) : null}
                </>
              ) : (
                <p className="capabilities-empty">NO DETAIL</p>
              )}
            </section>
          ) : (
            <section className="panel cap-detail cap-detail--empty">
              <p className="capabilities-empty">SELECT A CAPABILITY ON THE LEFT TO VIEW DETAILS</p>
            </section>
          )}
        </div>
      )}

      {modal?.kind === "new" ? (
        <NewProposalModal
          rest={rest}
          onDone={(msg) => {
            setModal(null);
            setNotice(msg);
            setError(null);
            void load();
          }}
          onClose={() => setModal(null)}
        />
      ) : null}
      {modal?.kind === "reject" ? (
        <RejectModal
          rest={rest}
          name={modal.name}
          onDone={(msg) => {
            if (selected?.staging && selected.name === modal.name) {
              setSelected(null);
              setDetail(null);
            }
            setModal(null);
            setNotice(msg);
            setError(null);
            void load();
          }}
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

/** One capability list card (cyan for trusted, yellow outline for staging). */
function CapCard({
  cap,
  staging,
  selected,
  onSelect,
}: {
  cap: CapabilitySummary;
  staging: boolean;
  selected: boolean;
  onSelect: () => void;
}): ReactElement {
  return (
    <div
      className={`cap-card${staging ? " cap-card--staging" : ""}${
        selected ? " cap-card--selected" : ""
      }`}
      onClick={onSelect}
    >
      <div className="cap-card-head">
        <div className={`cap-card-name${cap.deprecated ? " cap-card-name--deprecated" : ""}`}>
          {cap.name}
        </div>
        <span
          className={`cap-purity${cap.pure === false ? " cap-purity--impure" : ""}`}
        >
          {pureLabel(cap.pure)}
        </span>
        <span className={`cap-card-badge${staging ? " cap-card-badge--staging" : ""}`}>
          {staging ? "STAGING" : "TRUSTED"}
        </span>
      </div>
      {cap.deprecated ? (
        <span className="cap-deprecated-badge">DEPRECATED</span>
      ) : null}
      {cap.description ? <div className="cap-card-desc">{cap.description}</div> : null}
      <div className="tech-label">AUTHOR: {cap.author || "unknown"}</div>
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
    <div className="cap-modal-overlay" onClick={onClose}>
      <div
        className="panel cap-modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h3 className="tech-label cap-modal-title">{title}</h3>
        {children}
      </div>
    </div>
  );
}

/**
 * New staging proposal: artifact EDN full text + optional proposal note.
 * The artifact's :name is the capability's identity — no separate name
 * field. A failed validation chain reports the server's 400 message and
 * writes nothing.
 */
function NewProposalModal({
  rest,
  onDone,
  onClose,
}: {
  rest: RestClient;
  onDone: (notice: string) => void;
  onClose: () => void;
}): ReactElement {
  const [artifact, setArtifact] = useState("");
  const [proposalNote, setProposalNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    if (!artifact.trim()) {
      setError("ARTIFACT is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const data = (await rest.createStagingCapability({
        artifact,
        proposal_note: proposalNote.trim() || undefined,
      })) as { capability?: { name?: string } };
      onDone(`STAGED ${data.capability?.name ?? "capability"} // AWAITING HUMAN APPROVAL`);
    } catch (err) {
      setError(writeErrMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title="NEW PROPOSAL // CAPABILITY ARTIFACT" onClose={onClose}>
      <div className="cap-form">
        <label className="cap-form-row">
          <span className="tech-label">capability.tlon (full EDN text) *</span>
          <textarea
            className="input cap-form-artifact"
            value={artifact}
            autoFocus
            spellCheck={false}
            placeholder='{:version 1 :name "word-name" :description "…" :args [] :impl (fn [] …) :pure true}'
            onChange={(e) => setArtifact(e.target.value)}
          />
        </label>
        <label className="cap-form-row">
          <span className="tech-label">PROPOSAL NOTE (optional)</span>
          <input
            className="input"
            value={proposalNote}
            onChange={(e) => setProposalNote(e.target.value)}
          />
        </label>
      </div>
      {error ? <div className="capabilities-error-bar">{error}</div> : null}
      <div className="cap-modal-actions">
        <button type="button" className="btn btn--secondary" onClick={onClose} disabled={busy}>
          CANCEL
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "STAGING…" : "STAGE PROPOSAL"}
        </button>
      </div>
    </ModalShell>
  );
}

/** Reject confirmation dialog (reject deletes the proposal permanently). */
function RejectModal({
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
      await rest.rejectCapability(name);
      onDone(`REJECTED ${name}`);
    } catch (err) {
      setError(writeErrMsg(err));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`REJECT // ${name}`} onClose={onClose}>
      <p className="cap-reject-warning">
        REJECT CAPABILITY {name}? The staged proposal is deleted permanently.
      </p>
      {error ? <div className="capabilities-error-bar">{error}</div> : null}
      <div className="cap-modal-actions">
        <button type="button" className="btn btn--secondary" onClick={onClose} disabled={busy}>
          CANCEL
        </button>
        <button
          type="button"
          className="btn btn--danger"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "REJECTING…" : "CONFIRM REJECT"}
        </button>
      </div>
    </ModalShell>
  );
}
