import { useEffect, useRef, useState } from "react";
import type { ReactElement, UIEvent } from "react";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/events.css";

const MAX_EVENTS = 500;
/** Distance from the bottom (px) within which auto-follow stays engaged. */
const FOLLOW_THRESHOLD_PX = 24;

function formatTime(ev: ServerEvent): string {
  try {
    return new Date(ev.receivedAt).toLocaleTimeString("en-GB", { hour12: false });
  } catch {
    return "??:??:??";
  }
}

function eventJson(ev: ServerEvent): string {
  return JSON.stringify(
    {
      event_type: ev.eventType,
      source: ev.source,
      timestamp: ev.timestamp,
      trace_id: ev.traceId,
      span_id: ev.spanId,
      payload: ev.payload,
    },
    null,
    2,
  );
}

/**
 * EVENTS — terminal-style full event stream (pure SSE, no REST). Ring
 * buffer of 500 events; the terminal auto-follows the bottom until the
 * user scrolls up. PAUSE freezes the stream (incoming events are dropped
 * while paused); clicking a row expands the event's JSON payload.
 */
export function EventsView({ sse }: ViewProps): ReactElement {
  const [events, setEvents] = useState<ServerEvent[]>([]);
  const [paused, setPaused] = useState(false);
  const [follow, setFollow] = useState(true);
  const [expanded, setExpanded] = useState<ServerEvent | null>(null);
  // Ref mirror so the SSE subscription (bound once) sees the pause flag.
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onEvent = (ev: ServerEvent): void => {
      if (pausedRef.current) return;
      setEvents((prev) =>
        prev.length >= MAX_EVENTS
          ? [...prev.slice(prev.length - MAX_EVENTS + 1), ev]
          : [...prev, ev],
      );
    };
    sse.on("event", onEvent);
    return () => {
      sse.off("event", onEvent);
    };
  }, [sse]);

  // Auto-follow: stick to the bottom while `follow` holds.
  useEffect(() => {
    const el = termRef.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [events, follow]);

  const onScroll = (e: UIEvent<HTMLDivElement>): void => {
    const el = e.currentTarget;
    setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX);
  };

  const clear = (): void => {
    setEvents([]);
    setExpanded(null);
  };

  return (
    <section className="panel view-panel events-view">
      <div className="events-toolbar">
        <h2 className="view-title tech-label">EVENTS</h2>
        <span className="tech-label">{events.length} EVENTS</span>
        {paused ? <span className="tech-label events-flag">PAUSED</span> : null}
        {!follow && !paused ? (
          <span className="tech-label events-flag">FOLLOW OFF</span>
        ) : null}
        <span className="events-spacer" />
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => setPaused((prev) => !prev)}
        >
          {paused ? "RESUME" : "PAUSE"}
        </button>
        <button type="button" className="btn btn--danger" onClick={clear}>
          CLEAR
        </button>
      </div>
      <div className="terminal events-terminal" ref={termRef} onScroll={onScroll}>
        {events.length === 0 ? (
          <div className="terminal-line events-empty">waiting for events…</div>
        ) : null}
        {events.map((ev, index) => (
          <div key={`${ev.receivedAt}-${index}`}>
            <div
              className={`terminal-line event-row${expanded === ev ? " event-row--expanded" : ""}`}
              onClick={() => setExpanded(expanded === ev ? null : ev)}
            >
              <span className="event-time">{formatTime(ev)} </span>
              <span className="event-type">{ev.eventType}</span>
              {ev.source ? <span className="event-dim"> {ev.source}</span> : null}
              {ev.traceId ? (
                <span className="event-dim"> trace:{ev.traceId.slice(0, 8)}</span>
              ) : null}
            </div>
            {expanded === ev ? <pre className="event-json">{eventJson(ev)}</pre> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
