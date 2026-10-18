import type { ReactElement } from "react";
import { schedulingActions, schedulingDraining } from "../lib/agentScheduling";
import type { SchedulingAction, SchedulingStatus } from "../lib/agentScheduling";

export function AgentSchedulingActions({ status, busy = false, onAction }: {
  status: SchedulingStatus;
  busy?: boolean;
  onAction: (action: SchedulingAction) => void;
}): ReactElement {
  return <>{schedulingActions(status.state).map(({ action, label }) => <button
    key={action}
    type="button"
    className={`btn ${action === "terminate" ? "btn--danger" : "btn--secondary"}`}
    title={action === "wait" ? "当前工作自然结束后进入等待；未处理邮件可能使成员立即再次活跃。" : action === "wake" ? "提前结束睡眠；有待处理邮件则开始工作，否则进入等待。" : undefined}
    disabled={busy || schedulingDraining(status)}
    onClick={(event) => { event.stopPropagation(); onAction(action); }}
  >{label}</button>)}</>;
}
