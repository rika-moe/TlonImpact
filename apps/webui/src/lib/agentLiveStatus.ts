/** Per-agent LLM/tool history derived from SSE; current state comes from the server. */

import type { ServerEvent } from "../types";

export interface AgentLiveStatus {
  /** Duration of the last LLM call (ms). */
  lastLlmMs?: number;
  lastLlmModel?: string;
  lastTool?: { name: string; success: boolean; at: number };
}

export type LiveStatusMap = Record<string, AgentLiveStatus>;

const EMPTY: AgentLiveStatus = {};

type Payload = Record<string, unknown>;

function payloadOf(ev: ServerEvent): Payload {
  return typeof ev.payload === "object" && ev.payload !== null
    ? (ev.payload as Payload)
    : {};
}

function agentIdOf(ev: ServerEvent): string | undefined {
  const id = payloadOf(ev)["agent_id"];
  return typeof id === "string" && id.length > 0 ? id : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function withStatus(
  map: LiveStatusMap,
  id: string,
  patch: Partial<AgentLiveStatus>,
): LiveStatusMap {
  return { ...map, [id]: { ...(map[id] ?? EMPTY), ...patch } };
}

/**
 * Fold one event into the live-status map. Irrelevant events (and
 * relevant ones without an agent_id) return the map unchanged.
 */
export function applyEvent(map: LiveStatusMap, ev: ServerEvent): LiveStatusMap {
  const id = agentIdOf(ev);
  if (id === undefined) return map;
  const p = payloadOf(ev);

  switch (ev.eventType) {
    case "agent.llm_response": {
      const ms = num(p["duration_ms"]);
      const model = str(p["model"]);
      return withStatus(map, id, {
        ...(ms !== undefined ? { lastLlmMs: ms } : {}),
        ...(model !== undefined ? { lastLlmModel: model } : {}),
      });
    }
    case "tool.execute_end": {
      const name = str(p["tool_name"]);
      if (name === undefined) return map;
      return withStatus(map, id, {
        lastTool: { name, success: p["success"] !== false, at: ev.receivedAt },
      });
    }
    case "tool.error": {
      const name = str(p["tool_name"]);
      if (name === undefined) return map;
      return withStatus(map, id, {
        lastTool: { name, success: false, at: ev.receivedAt },
      });
    }
    default:
      return map;
  }
}
