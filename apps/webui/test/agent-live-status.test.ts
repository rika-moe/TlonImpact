import assert from "node:assert/strict";
import { test } from "node:test";
import { applyEvent, type LiveStatusMap } from "../src/lib/agentLiveStatus";
import type { ServerEvent } from "../src/types";

function ev(eventType: string, payload: unknown, receivedAt = 1000): ServerEvent {
  return { eventType, payload, receivedAt };
}

test("thinking_start opens the flag, thinking_end closes it with the duration", () => {
  let map: LiveStatusMap = {};
  map = applyEvent(
    map,
    ev("agent.thinking_start", { agent_id: "a", context_length: 3 }),
  );
  assert.equal(map["a"]?.thinking, true);
  assert.equal(map["a"]?.lastThinkMs, undefined);

  map = applyEvent(map, ev("agent.thinking_end", { agent_id: "a", duration_ms: 1800 }));
  assert.equal(map["a"]?.thinking, false);
  assert.equal(map["a"]?.lastThinkMs, 1800);
});

test("llm_response records duration and model without touching thinking", () => {
  let map: LiveStatusMap = applyEvent(
    {},
    ev("agent.thinking_start", { agent_id: "a" }),
  );
  map = applyEvent(
    map,
    ev("agent.llm_response", { agent_id: "a", duration_ms: 2300, model: "openai/gpt-5" }),
  );
  assert.equal(map["a"]?.thinking, true);
  assert.equal(map["a"]?.lastLlmMs, 2300);
  assert.equal(map["a"]?.lastLlmModel, "openai/gpt-5");
});

test("tool.execute_end records the last tool with its success flag", () => {
  let map: LiveStatusMap = applyEvent(
    {},
    ev("tool.execute_end", { agent_id: "a", tool_name: "sendEmail", success: true }, 5000),
  );
  assert.deepEqual(map["a"]?.lastTool, { name: "sendEmail", success: true, at: 5000 });

  map = applyEvent(
    map,
    ev("tool.execute_end", { agent_id: "a", tool_name: "readFile", success: false }, 6000),
  );
  assert.deepEqual(map["a"]?.lastTool, { name: "readFile", success: false, at: 6000 });
});

test("tool.error records a failed tool call", () => {
  const map = applyEvent(
    {},
    ev("tool.error", { agent_id: "a", tool_name: "readFile", error: "enoent" }, 7000),
  );
  assert.deepEqual(map["a"]?.lastTool, { name: "readFile", success: false, at: 7000 });
});

test("agent.stopped and agent.sleeping clear the thinking flag", () => {
  let map: LiveStatusMap = applyEvent({}, ev("agent.thinking_start", { agent_id: "a" }));
  map = applyEvent(map, ev("agent.stopped", { agent_id: "a", stopped_by: "adam" }));
  assert.equal(map["a"]?.thinking, false);

  map = applyEvent(map, ev("agent.thinking_start", { agent_id: "a" }));
  map = applyEvent(map, ev("agent.sleeping", { agent_id: "a", idle_seconds: 30 }));
  assert.equal(map["a"]?.thinking, false);
});

test("events are tracked per agent independently", () => {
  let map: LiveStatusMap = applyEvent({}, ev("agent.thinking_start", { agent_id: "a" }));
  map = applyEvent(map, ev("agent.thinking_start", { agent_id: "b" }));
  map = applyEvent(map, ev("agent.thinking_end", { agent_id: "a", duration_ms: 100 }));
  assert.equal(map["a"]?.thinking, false);
  assert.equal(map["b"]?.thinking, true);
});

test("irrelevant events and missing agent_id return the map unchanged", () => {
  const map: LiveStatusMap = { a: { thinking: true } };
  assert.equal(applyEvent(map, ev("mail.new", { from_address: "x@tlon.internal" })), map);
  assert.equal(applyEvent(map, ev("config.updated", { agent_id: "a" })), map);
  assert.equal(applyEvent(map, ev("system.startup", { agents: 2 })), map);
  // Relevant type but no agent_id in the payload: nothing to key on.
  assert.equal(applyEvent(map, ev("agent.thinking_start", {})), map);
  assert.equal(applyEvent(map, ev("agent.thinking_start", null)), map);
});

test("missing duration fields leave the previous value in place", () => {
  let map: LiveStatusMap = applyEvent(
    {},
    ev("agent.thinking_end", { agent_id: "a", duration_ms: 100 }),
  );
  map = applyEvent(map, ev("agent.thinking_end", { agent_id: "a" }));
  assert.equal(map["a"]?.lastThinkMs, 100);
});
