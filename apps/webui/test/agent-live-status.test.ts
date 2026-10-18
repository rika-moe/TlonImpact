import assert from "node:assert/strict";
import { test } from "node:test";
import { applyEvent, type LiveStatusMap } from "../src/lib/agentLiveStatus";
import type { ServerEvent } from "../src/types";

function ev(eventType: string, payload: unknown, receivedAt = 1000): ServerEvent {
  return { eventType, payload, receivedAt };
}

test("llm_response records duration and model", () => {
  const map = applyEvent({}, ev("agent.llm_response", {
    agent_id: "a", duration_ms: 2300, model: "openai/gpt-5",
  }));
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

test("events are tracked per agent independently", () => {
  let map = applyEvent({}, ev("agent.llm_response", { agent_id: "a", duration_ms: 100 }));
  map = applyEvent(map, ev("agent.llm_response", { agent_id: "b", duration_ms: 200 }));
  map = applyEvent(map, ev("agent.llm_response", { agent_id: "a", duration_ms: 300 }));
  assert.equal(map["a"]?.lastLlmMs, 300);
  assert.equal(map["b"]?.lastLlmMs, 200);
});

test("irrelevant events and missing agent_id return the map unchanged", () => {
  const map: LiveStatusMap = { a: { lastLlmMs: 100 } };
  assert.equal(applyEvent(map, ev("mail.new", { from_address: "x@tlon.internal" })), map);
  assert.equal(applyEvent(map, ev("config.updated", { agent_id: "a" })), map);
  assert.equal(applyEvent(map, ev("system.startup", { agents: 2 })), map);
  for (const eventType of ["agent.thinking_start", "agent.thinking_end", "agent.stopped", "agent.sleeping"]) {
    assert.equal(applyEvent(map, ev(eventType, { agent_id: "a" })), map);
  }
  // Relevant type but no agent_id in the payload: nothing to key on.
  assert.equal(applyEvent(map, ev("agent.llm_response", {})), map);
  assert.equal(applyEvent(map, ev("agent.llm_response", null)), map);
});

test("missing duration fields leave the previous value in place", () => {
  let map: LiveStatusMap = applyEvent(
    {},
    ev("agent.llm_response", { agent_id: "a", duration_ms: 100 }),
  );
  map = applyEvent(map, ev("agent.llm_response", { agent_id: "a" }));
  assert.equal(map["a"]?.lastLlmMs, 100);
});
