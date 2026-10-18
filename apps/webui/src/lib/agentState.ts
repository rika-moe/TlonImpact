/** Scheduling states are independent of runtime residency and I/O phases. */
export type AgentState = "active" | "waiting" | "sleeping" | "dead";
export type StateTone = "cyan" | "dim" | "magenta" | "yellow";

export function stateTone(state: AgentState): StateTone {
  return { active: "cyan", waiting: "yellow", sleeping: "dim", dead: "magenta" }[state] as StateTone;
}

export function wakeLabel(wakeAt: string | null | undefined): string {
  if (!wakeAt) return "";
  return `WAKE AT ${new Date(wakeAt).toLocaleString()}`;
}
