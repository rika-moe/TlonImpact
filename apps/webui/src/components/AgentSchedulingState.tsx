import type { ReactElement } from "react";
import { pendingSchedulingLabel } from "../lib/agentScheduling";
import type { AgentState } from "../lib/agentState";
import { stateTone, wakeLabel } from "../lib/agentState";

export function StateBadge({ state }: { state: AgentState }): ReactElement {
  return <span className={`state-badge state-badge--${stateTone(state)}${state === "active" ? " state-badge--active" : ""}`}>
    {state}
  </span>;
}

/** Scheduling state and runtime residency are separate user-facing facts. */
export function SchedulingInfo({ status }: { status: {
  state: AgentState;
  wake_at?: string | null;
  waking?: boolean;
  stop_pending?: boolean;
  scheduling_pending?: string | null;
  error?: string | null;
} }): ReactElement {
  return <>
    {status.state === "sleeping" ? <div className="tech-label">{wakeLabel(status.wake_at)}</div> : null}
    {pendingSchedulingLabel(status) ? <div className="tech-label">{pendingSchedulingLabel(status)}</div> : null}
    {status.waking ? <div className="tech-label">LOADING RUNTIME</div> : null}
    {status.error ? <div className="agents-error-bar">{status.error}</div> : null}
  </>;
}
