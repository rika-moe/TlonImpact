/**
 * SSE event -> activity feed entry mapping (pure functions, unit-tested).
 *
 * Payload shapes are verified against the server's publish points
 * (src/tlon/agents/stages/services.py, agents/spawn.py, agents/act.py,
 * agents/loop_driver.py, agents/agent_registry.py, agents/reaper.py,
 * app/server/serverapp.py, organization/mail/delivery.py,
 * base/config.py). Unknown event types degrade to kind "unknown" with
 * the raw type name as the title.
 */

import type { ServerEvent } from "../types";

export type ActivityKind =
  | "mail"
  | "thinking"
  | "llm"
  | "tool"
  | "lifecycle"
  | "config"
  | "system"
  | "unknown";

/** One semantic feed row derived from a raw ServerEvent. */
export interface ActivityEntry {
  kind: ActivityKind;
  /** Human-readable one-liner, e.g. "林知遥 is thinking (12 context messages)". */
  title: string;
  /** Secondary fields, rendered dimmed. */
  detail?: string;
  agentId?: string;
  /** Local receive time, ms since epoch (drives row time display). */
  timestamp: number;
  /** The untouched source event (expandable JSON). */
  raw: ServerEvent;
}

/** Format a duration_ms payload field as seconds, e.g. 2300 -> "2.3s". */
export function formatDurationMs(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

type Payload = Record<string, unknown>;

function payloadOf(ev: ServerEvent): Payload {
  return typeof ev.payload === "object" && ev.payload !== null
    ? (ev.payload as Payload)
    : {};
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/** Local part of a mail address ("guest@tlon.internal" -> "guest"). */
function localPart(address: string): string {
  const at = address.indexOf("@");
  return at > 0 ? address.slice(0, at) : address;
}

/** Display name for the agent of an event: agent_name > name > agent_id. */
function agentName(p: Payload): string {
  return str(p["agent_name"]) ?? str(p["name"]) ?? str(p["agent_id"]) ?? "?";
}

function entry(
  kind: ActivityKind,
  title: string,
  ev: ServerEvent,
  detail?: string,
  agentId?: string,
): ActivityEntry {
  return { kind, title, detail, agentId, timestamp: ev.receivedAt, raw: ev };
}

function mailEntry(ev: ServerEvent, p: Payload): ActivityEntry {
  const mail = (typeof p["mail"] === "object" && p["mail"] !== null
    ? p["mail"]
    : p) as Payload;
  const from = str(mail["from_address"]);
  const subject = str(mail["subject"]);
  const recipient = str(p["recipient"]);
  const fromText = from !== undefined ? ` · from ${localPart(from)}` : "";
  const toText =
    ev.eventType === "mail.delivered" && recipient !== undefined
      ? ` → ${recipient}`
      : "";
  return entry("mail", `New mail${fromText}${toText}`, ev, subject);
}

function toolDetail(p: Payload): string | undefined {
  const parts: string[] = [];
  const args = p["arguments"];
  if (args !== undefined) {
    const text = JSON.stringify(args);
    if (text !== undefined && text !== "{}") {
      parts.push(text.length > 80 ? `${text.slice(0, 80)}…` : text);
    }
  }
  const error = str(p["error"]);
  if (error !== undefined) parts.push(error);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/** Map one ServerEvent to its activity feed entry. */
export function toActivity(ev: ServerEvent): ActivityEntry {
  const p = payloadOf(ev);
  const agentId = str(p["agent_id"]);
  const name = agentName(p);

  switch (ev.eventType) {
    case "mail.new":
    case "mail.delivered":
      return mailEntry(ev, p);

    case "agent.thinking_start": {
      const contextLength = num(p["context_length"]);
      const suffix =
        contextLength !== undefined ? ` (${contextLength} context messages)` : "";
      return entry("thinking", `${name} is thinking${suffix}`, ev, undefined, agentId);
    }
    case "agent.thinking_end": {
      const ms = num(p["duration_ms"]);
      const suffix = ms !== undefined ? ` · ${formatDurationMs(ms)}` : "";
      return entry("thinking", `${name} finished thinking${suffix}`, ev, undefined, agentId);
    }

    case "agent.llm_request": {
      const model = str(p["model"]);
      const messages = num(p["messages_count"]);
      const tools = num(p["tools_count"]);
      const parts: string[] = [];
      if (messages !== undefined) parts.push(`${messages} messages`);
      if (tools !== undefined) parts.push(`${tools} tools`);
      return entry(
        "llm",
        `${name} LLM request${model !== undefined ? ` · ${model}` : ""}`,
        ev,
        parts.length > 0 ? parts.join(" · ") : undefined,
        agentId,
      );
    }
    case "agent.llm_response": {
      const ms = num(p["duration_ms"]);
      const toolCalls = num(p["tool_call_count"]);
      const parts: string[] = [];
      if (toolCalls !== undefined && toolCalls > 0) parts.push(`${toolCalls} tool calls`);
      const finish = str(p["finish_reason"]);
      if (finish !== undefined) parts.push(finish);
      const model = str(p["model"]);
      if (model !== undefined) parts.push(model);
      return entry(
        "llm",
        `${name} LLM response${ms !== undefined ? ` ${formatDurationMs(ms)}` : ""}`,
        ev,
        parts.length > 0 ? parts.join(" · ") : undefined,
        agentId,
      );
    }

    case "tool.execute_start":
      return entry("tool", `${name} is calling tool ${str(p["tool_name"]) ?? "?"}`, ev, toolDetail(p), agentId);
    case "tool.execute_end": {
      const ok = p["success"] !== false;
      return entry(
        "tool",
        `${name} tool ${str(p["tool_name"]) ?? "?"} ${ok ? "succeeded" : "failed"}`,
        ev,
        undefined,
        agentId,
      );
    }
    case "tool.error":
      return entry(
        "tool",
        `${name} tool ${str(p["tool_name"]) ?? "?"} error`,
        ev,
        toolDetail(p),
        agentId,
      );

    case "agent.waking":
      return entry("lifecycle", `${name} is waking`, ev, undefined, agentId);
    case "agent.woken": {
      const secs = num(p["wake_duration_seconds"]);
      return entry(
        "lifecycle",
        `${name} woke up${secs !== undefined ? ` · ${secs.toFixed(1)}s` : ""}`,
        ev,
        undefined,
        agentId,
      );
    }
    case "agent.wake_failed":
      return entry("lifecycle", `${name} failed to wake`, ev, undefined, agentId);
    case "agent.sleeping": {
      const idle = num(p["idle_seconds"]);
      return entry(
        "lifecycle",
        `${name} is going to sleep`,
        ev,
        idle !== undefined ? `idle ${Math.round(idle)}s` : undefined,
        agentId,
      );
    }
    case "agent.idle": {
      const idle = num(p["idle_seconds"]);
      return entry(
        "lifecycle",
        `${name} idle${idle !== undefined ? ` ${Math.round(idle)}s` : ""}`,
        ev,
        undefined,
        agentId,
      );
    }
    case "agent.started":
      return entry("lifecycle", `${name} started`, ev, undefined, agentId);
    case "agent.stopped": {
      const stoppedBy = str(p["stopped_by"]);
      return entry(
        "lifecycle",
        `${name} stopped`,
        ev,
        stoppedBy !== undefined ? `by ${stoppedBy}` : undefined,
        agentId,
      );
    }
    case "agent.spawned": {
      const parts: string[] = [];
      const prototype = str(p["prototype"]);
      if (prototype !== undefined) parts.push(`prototype ${prototype}`);
      const depth = num(p["spawn_depth"]);
      if (depth !== undefined) parts.push(`GEN ${depth}`);
      const spawner = str(p["spawner_id"]);
      if (spawner !== undefined) parts.push(`by ${spawner}`);
      return entry(
        "lifecycle",
        `${name} spawned`,
        ev,
        parts.length > 0 ? parts.join(" · ") : undefined,
        agentId,
      );
    }

    case "config.updated":
      return entry("config", `Configuration updated · ${agentId ?? "?"}`, ev, str(p["action"]), agentId);

    case "system.startup": {
      const count = num(p["agents"]);
      return entry(
        "system",
        "System started",
        ev,
        count !== undefined ? `${count} agents` : undefined,
      );
    }
    case "system.shutdown":
      return entry("system", "System shut down", ev);

    default:
      return entry("unknown", ev.eventType, ev);
  }
}
