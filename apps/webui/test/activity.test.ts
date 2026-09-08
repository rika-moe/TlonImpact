import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDurationMs, toActivity } from "../src/lib/activity";
import type { ServerEvent } from "../src/types";

function ev(eventType: string, payload: unknown, receivedAt = 1000): ServerEvent {
  return { eventType, payload, receivedAt };
}

test("formatDurationMs renders milliseconds as seconds", () => {
  assert.equal(formatDurationMs(2300), "2.3s");
  assert.equal(formatDurationMs(0), "0.0s");
  assert.equal(formatDurationMs(42), "0.0s");
});

test("mail.new maps to a mail entry with sender and subject", () => {
  const item = toActivity(
    ev("mail.new", { from_address: "guest@tlon.internal", subject: "你好" }),
  );
  assert.equal(item.kind, "mail");
  assert.equal(item.title, "新邮件 · 来自 guest");
  assert.equal(item.detail, "你好");
  assert.equal(item.agentId, undefined);
});

test("mail.delivered maps to a mail entry with sender and recipient", () => {
  const item = toActivity(
    ev("mail.delivered", {
      mail: { from_address: "lin@tlon.internal", subject: null },
      recipient: "gu-yanxi",
    }),
  );
  assert.equal(item.kind, "mail");
  assert.equal(item.title, "新邮件 · 来自 lin → gu-yanxi");
  assert.equal(item.detail, undefined);
});

test("agent.thinking_start/end map to thinking entries", () => {
  const start = toActivity(
    ev("agent.thinking_start", {
      agent_id: "lin-zhiyao",
      agent_name: "林知遥",
      context_length: 12,
    }),
  );
  assert.equal(start.kind, "thinking");
  assert.equal(start.title, "林知遥 正在思考（上下文 12 条）");
  assert.equal(start.agentId, "lin-zhiyao");

  const end = toActivity(
    ev("agent.thinking_end", {
      agent_id: "lin-zhiyao",
      agent_name: "林知遥",
      duration_ms: 1800,
    }),
  );
  assert.equal(end.kind, "thinking");
  assert.equal(end.title, "林知遥 思考结束 · 1.8s");
});

test("agent.llm_request maps to an llm entry with model and counts", () => {
  const item = toActivity(
    ev("agent.llm_request", {
      agent_id: "xie-yunshu",
      model: "openai/gpt-5",
      messages_count: 9,
      tools_count: 4,
    }),
  );
  assert.equal(item.kind, "llm");
  assert.equal(item.title, "xie-yunshu LLM 请求 · openai/gpt-5");
  assert.equal(item.detail, "9 条消息 · 4 个工具");
});

test("agent.llm_response formats duration, tool calls and finish reason", () => {
  const item = toActivity(
    ev("agent.llm_response", {
      agent_id: "xie-yunshu",
      model: "openai/gpt-5",
      duration_ms: 2300,
      finish_reason: "tool_calls",
      has_tool_calls: true,
      tool_call_count: 3,
    }),
  );
  assert.equal(item.kind, "llm");
  assert.equal(item.title, "xie-yunshu LLM 调用 2.3s");
  assert.equal(item.detail, "3 次工具调用 · tool_calls · openai/gpt-5");
});

test("agent.llm_response omits the tool-call detail when zero", () => {
  const item = toActivity(
    ev("agent.llm_response", {
      agent_id: "a",
      duration_ms: 500,
      finish_reason: "stop",
      tool_call_count: 0,
    }),
  );
  assert.equal(item.title, "a LLM 调用 0.5s");
  assert.equal(item.detail, "stop");
});

test("tool events map to tool entries with success/failure", () => {
  const start = toActivity(
    ev("tool.execute_start", {
      agent_id: "a",
      tool_name: "sendEmail",
      arguments: { to: "guest" },
    }),
  );
  assert.equal(start.kind, "tool");
  assert.equal(start.title, "a 调用工具 sendEmail");
  assert.equal(start.detail, '{"to":"guest"}');

  const ok = toActivity(
    ev("tool.execute_end", { agent_id: "a", tool_name: "sendEmail", success: true }),
  );
  assert.equal(ok.title, "a 工具 sendEmail 成功");

  const fail = toActivity(
    ev("tool.execute_end", { agent_id: "a", tool_name: "readFile", success: false }),
  );
  assert.equal(fail.title, "a 工具 readFile 失败");

  const error = toActivity(
    ev("tool.error", { agent_id: "a", tool_name: "readFile", error: "enoent" }),
  );
  assert.equal(error.title, "a 工具 readFile 出错");
  assert.equal(error.detail, "enoent");
});

test("lifecycle events map with their payload specifics", () => {
  const woken = toActivity(
    ev("agent.woken", { agent_id: "a", wake_duration_seconds: 1.234 }),
  );
  assert.equal(woken.kind, "lifecycle");
  assert.equal(woken.title, "a 已唤醒 · 1.2s");

  assert.equal(toActivity(ev("agent.waking", { agent_id: "a" })).title, "a 正在唤醒");
  assert.equal(toActivity(ev("agent.wake_failed", { agent_id: "a" })).title, "a 唤醒失败");

  const sleeping = toActivity(
    ev("agent.sleeping", { agent_id: "a", idle_seconds: 61.6 }),
  );
  assert.equal(sleeping.title, "a 进入休眠");
  assert.equal(sleeping.detail, "空闲 62s");

  assert.equal(
    toActivity(ev("agent.idle", { agent_id: "a", idle_seconds: 90 })).title,
    "a 空闲 90s",
  );

  const started = toActivity(
    ev("agent.started", { agent_id: "a", name: "林知遥" }),
  );
  assert.equal(started.title, "林知遥 已启动");
  assert.equal(started.detail, undefined);

  const stopped = toActivity(
    ev("agent.stopped", { agent_id: "a", stopped_by: "adam" }),
  );
  assert.equal(stopped.title, "a 已停止");
  assert.equal(stopped.detail, "由 adam");

  const spawned = toActivity(
    ev("agent.spawned", {
      agent_id: "b",
      name: "谢云舒",
      prototype: "writer",
      spawner_id: "a",
      spawn_depth: 1,
      started: true,
    }),
  );
  assert.equal(spawned.title, "谢云舒 已孵化");
  assert.equal(spawned.detail, "原型 writer · GEN 1 · 由 a");
});

test("config.updated maps to a config entry", () => {
  const item = toActivity(ev("config.updated", { agent_id: "a", action: "update" }));
  assert.equal(item.kind, "config");
  assert.equal(item.title, "配置已更新 · a");
  assert.equal(item.detail, "update");
});

test("system events map to system entries", () => {
  const up = toActivity(ev("system.startup", { agents: 3 }));
  assert.equal(up.kind, "system");
  assert.equal(up.title, "系统启动");
  assert.equal(up.detail, "3 个成员");

  const down = toActivity(ev("system.shutdown", {}));
  assert.equal(down.kind, "system");
  assert.equal(down.title, "系统关闭");
});

test("unknown event types degrade to kind unknown with the raw type name", () => {
  const item = toActivity(ev("agent.join", { agent_id: "a" }));
  assert.equal(item.kind, "unknown");
  assert.equal(item.title, "agent.join");
  assert.equal(item.raw.eventType, "agent.join");
});

test("non-object payloads never throw", () => {
  const item = toActivity(ev("mail.new", null));
  assert.equal(item.kind, "mail");
  assert.equal(item.title, "新邮件");

  const bare = toActivity(ev("agent.thinking_start", "oops"));
  assert.equal(bare.title, "? 正在思考");
  assert.equal(bare.agentId, undefined);
});

test("timestamp comes from receivedAt and raw keeps the source event", () => {
  const source = ev("system.shutdown", {}, 4242);
  const item = toActivity(source);
  assert.equal(item.timestamp, 4242);
  assert.equal(item.raw, source);
});
