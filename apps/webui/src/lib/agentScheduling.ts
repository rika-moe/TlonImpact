import type { AgentState } from "./agentState";
import type { AgentSchedulingResponse } from "../client/rest";

export type SchedulingAction = "wake" | "wait" | "sleep" | "terminate";
export type SchedulingStatus = {
  state: AgentState;
  stop_pending?: boolean;
  scheduling_pending?: string | null;
};

export function schedulingDraining(status: SchedulingStatus): boolean {
  return Boolean(status.stop_pending || (status.state === "active" && status.scheduling_pending));
}

export function pendingSchedulingLabel(status: SchedulingStatus): string | null {
  if (!schedulingDraining(status)) return null;
  const action = status.stop_pending ? "terminate" : status.scheduling_pending;
  const names: Record<string, string> = { wait: "进入等待", finish: "进入等待", sleep: "定时睡眠", exit: "永久退出", terminate: "永久退出" };
  const name = names[action ?? ""] ?? "状态调整";
  return `${name}待执行 · 正在等待当前工作结束`;
}

export function schedulingActions(state: AgentState): { action: SchedulingAction; label: string }[] {
  switch (state) {
    case "active": return [{ action: "wait", label: "进入等待" }, { action: "sleep", label: "定时睡眠" }, { action: "terminate", label: "永久退出" }];
    case "waiting": return [{ action: "sleep", label: "定时睡眠" }, { action: "terminate", label: "永久退出" }];
    case "sleeping": return [{ action: "wake", label: "唤醒" }, { action: "sleep", label: "调整醒来时间" }, { action: "terminate", label: "永久退出" }];
    case "dead": return [];
  }
}

/** Browser-local input is converted to an unambiguous UTC deadline. */
export function sleepDeadline(mode: "duration" | "time", value: string, now = Date.now()): string {
  const deadline = mode === "duration" ? now + Number(value) * 60_000 : new Date(value).getTime();
  if (!value.trim() || !Number.isFinite(deadline) || deadline <= now) {
    throw new Error("请选择有效的未来醒来时间或正数睡眠分钟数。");
  }
  return new Date(deadline).toISOString();
}

export function schedulingNotice(action: SchedulingAction, result: AgentSchedulingResponse): string {
  if (result.status === "pending") return "请求已接受，正在等待当前工作结束；实际状态以成员状态为准。";
  if (action === "wake") return "已唤醒；有待处理邮件时开始工作，否则进入等待。";
  if (action === "wait") return "已进入等待；未处理邮件可能使成员立即再次活跃。";
  if (action === "sleep") return "睡眠时间已设置；期间正常收信，到期或管理员唤醒后处理。";
  return "成员已永久退出，拒收新邮件，历史记录保留。";
}
