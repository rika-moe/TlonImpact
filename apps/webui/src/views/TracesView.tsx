import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import type { ViewProps } from "../types";
import "../styles/traces.css";

type TraceRecord = Record<string, unknown>;

/** A node of the span tree returned by GET /traces/{id} (`span_tree`). */
interface SpanNode {
  span_id?: string;
  name?: string;
  source?: string;
  event_type?: string;
  duration_ms?: number;
  payload?: unknown;
  metadata?: unknown;
  children?: SpanNode[];
}

interface TraceDetail {
  trace?: TraceRecord;
  span_tree?: SpanNode;
}

function traceId(trace: TraceRecord): string {
  return String(trace["trace_id"] ?? trace["id"] ?? "?");
}

function traceLabel(trace: TraceRecord): string {
  const id = traceId(trace);
  const extras: string[] = [];
  for (const key of ["name", "source", "status", "root_agent"]) {
    const value = trace[key];
    if (typeof value === "string" && value) extras.push(value);
  }
  return extras.length > 0 ? `${id.slice(0, 8)} (${extras.join(" · ")})` : id.slice(0, 8);
}

function formatStartTime(trace: TraceRecord): string {
  const raw = trace["start_time"];
  if (typeof raw !== "string") return "";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toLocaleString("en-GB", { hour12: false });
}

/** JSON shown when a span row is expanded (only defined fields included). */
function spanJson(node: SpanNode): string {
  const out: Record<string, unknown> = {};
  if (node.payload !== undefined) out["payload"] = node.payload;
  if (node.metadata !== undefined) out["metadata"] = node.metadata;
  return JSON.stringify(out, null, 2);
}

/** One span row plus its descendants (indentation encodes depth). Rows
 *  with a payload/metadata expand on click (mirrors events/logs views). */
function SpanTreeRows({ node, depth }: { node: SpanNode; depth: number }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const expandable = node.payload !== undefined || node.metadata !== undefined;
  return (
    <>
      <div
        className={`span-row${expandable ? " span-row--expandable" : ""}${
          expanded ? " span-row--expanded" : ""
        }`}
        style={{ paddingLeft: depth * 18 }}
        onClick={expandable ? () => setExpanded((prev) => !prev) : undefined}
      >
        <span className="span-name">{node.name ?? "(unnamed)"}</span>
        {node.source ? <span className="span-dim">{node.source}</span> : null}
        {node.event_type ? <span className="span-dim">{node.event_type}</span> : null}
        <span className="traces-spacer" />
        {typeof node.duration_ms === "number" ? (
          <span className="span-duration">{Math.round(node.duration_ms)}ms</span>
        ) : (
          <span className="span-dim">open</span>
        )}
      </div>
      {expanded && expandable ? (
        <pre className="span-json" style={{ marginLeft: depth * 18 }}>
          {spanJson(node)}
        </pre>
      ) : null}
      {(node.children ?? []).map((child, index) => (
        <SpanTreeRows key={child.span_id ?? index} node={child} depth={depth + 1} />
      ))}
    </>
  );
}

/**
 * TRACES — trace list (GET /traces?limit=100) + span tree detail
 * (GET /traces/{id}). Left list, right detail; REFRESH reloads the list.
 */
export function TracesView({ rest }: ViewProps): ReactElement {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTraces(await rest.listTraces(100));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  const openTrace = (trace: TraceRecord): void => {
    const id = traceId(trace);
    setSelectedId(id);
    setDetail(null);
    setLoadingDetail(true);
    rest
      .getTrace(id)
      .then((data) => setDetail(data as TraceDetail))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoadingDetail(false));
  };

  const spanTree = detail?.span_tree;
  const hasSpans = spanTree !== undefined && Object.keys(spanTree).length > 0;

  return (
    <section className="traces-view">
      <div className="traces-toolbar">
        <h2 className="tech-label traces-title">TRACES</h2>
        <span className="tech-label">{traces.length} TRACES</span>
        {error ? <span className="tech-label traces-error">{error}</span> : null}
        <span className="traces-spacer" />
        <button type="button" className="btn btn--secondary" onClick={() => void load()}>
          REFRESH
        </button>
      </div>
      <div className="traces-body">
        <div className="panel traces-list">
          {traces.length === 0 ? (
            <div className="tech-label traces-empty">NO TRACES</div>
          ) : (
            traces.map((trace) => {
              const id = traceId(trace);
              return (
                <div
                  key={id}
                  className={`trace-item${id === selectedId ? " trace-item--active" : ""}`}
                  title={id}
                  onClick={() => openTrace(trace)}
                >
                  <span className="trace-item-id">{traceLabel(trace)}</span>
                  <span className="trace-item-time"> {formatStartTime(trace)}</span>
                </div>
              );
            })
          )}
        </div>
        <div className="panel traces-detail">
          {selectedId === null ? (
            <div className="tech-label traces-empty">SELECT A TRACE</div>
          ) : loadingDetail ? (
            <div className="tech-label traces-empty">LOADING…</div>
          ) : detail === null ? null : (
            <>
              {detail.trace ? (
                <div className="trace-meta">
                  <div className="trace-meta-row">
                    <span className="tech-label">TRACE</span>
                    <span className="trace-meta-value">{traceId(detail.trace)}</span>
                  </div>
                  <div className="trace-meta-row">
                    <span className="tech-label">NAME</span>
                    <span className="trace-meta-value">
                      {String(detail.trace["name"] ?? "-")}
                    </span>
                  </div>
                  <div className="trace-meta-row">
                    <span className="tech-label">SOURCE</span>
                    <span className="trace-meta-value">
                      {String(detail.trace["source"] ?? "-")}
                    </span>
                  </div>
                  <div className="trace-meta-row">
                    <span className="tech-label">DURATION</span>
                    <span className="trace-meta-value">
                      {typeof detail.trace["duration_ms"] === "number"
                        ? `${Math.round(detail.trace["duration_ms"])}ms`
                        : "open"}
                    </span>
                  </div>
                </div>
              ) : null}
              {hasSpans && spanTree ? (
                <div className="span-tree">
                  <SpanTreeRows node={spanTree} depth={0} />
                </div>
              ) : (
                <pre className="traces-json">{JSON.stringify(detail, null, 2)}</pre>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
