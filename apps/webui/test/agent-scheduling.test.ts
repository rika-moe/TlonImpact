import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AgentSchedulingActions } from "../src/components/AgentSchedulingActions";
import { SchedulingInfo } from "../src/components/AgentSchedulingState";
import { RestClient } from "../src/client/rest";
import type { AgentSchedulingResponse } from "../src/client/rest";
import { pendingSchedulingLabel, schedulingNotice, sleepDeadline } from "../src/lib/agentScheduling";

const onAction = (): void => {};

test("state-specific action buttons never offer an artificial active transition", () => {
  const cases = [
    ["active", ["进入等待", "定时睡眠", "永久退出"], ["唤醒", "调整醒来时间"]],
    ["waiting", ["定时睡眠", "永久退出"], ["唤醒", "进入等待"]],
    ["sleeping", ["唤醒", "调整醒来时间", "永久退出"], ["进入等待", "定时睡眠"]],
    ["dead", [], ["唤醒", "永久退出", "定时睡眠", "进入等待"]],
  ] as const;
  for (const [state, present, absent] of cases) {
    const html = renderToStaticMarkup(createElement(AgentSchedulingActions, { status: { state }, onAction }));
    for (const label of present) assert.ok(html.includes(`>${label}</button>`), `${state}: ${label}`);
    for (const label of absent) assert.ok(!html.includes(`>${label}</button>`), `${state}: ${label}`);
    assert.ok(!html.includes("变活跃"));
  }
});

test("draining disables repeat requests and explains the pending action", () => {
  for (const scheduling_pending of ["wait", "sleep", "exit"]) {
    const status = { state: "active" as const, scheduling_pending };
    const html = renderToStaticMarkup(createElement(AgentSchedulingActions, { status, onAction }));
    assert.equal((html.match(/disabled=""/g) ?? []).length, 3);
    const diagnostic = renderToStaticMarkup(createElement(SchedulingInfo, { status }));
    assert.ok(diagnostic.includes("待执行"));
    assert.ok(diagnostic.includes("当前工作结束"));
  }
  const sleeping = { state: "sleeping" as const, scheduling_pending: "sleep" };
  const html = renderToStaticMarkup(createElement(AgentSchedulingActions, { status: sleeping, onAction }));
  assert.ok(!html.includes("disabled"));
  assert.equal(pendingSchedulingLabel(sleeping), null);
  const stopping = renderToStaticMarkup(createElement(AgentSchedulingActions, {
    status: { state: "sleeping", stop_pending: true }, onAction,
  }));
  assert.equal((stopping.match(/disabled=""/g) ?? []).length, 3);
});

test("sleep deadline accepts positive durations and future local times as UTC", () => {
  const now = new Date("2030-01-01T00:00:00Z").getTime();
  assert.equal(sleepDeadline("duration", "15", now), "2030-01-01T00:15:00.000Z");
  const local = "2030-01-02T12:30";
  assert.equal(sleepDeadline("time", local, now), new Date(local).toISOString());
  for (const value of ["", " ", "0", "-1", "Infinity", "hello"]) {
    assert.throws(() => sleepDeadline("duration", value, now), /未来/);
  }
  for (const value of ["", "nonsense", "2029-01-01T00:00", "2030-01-01T00:00:00Z"]) {
    assert.throws(() => sleepDeadline("time", value, now), /未来/);
  }
});

test("management REST uses separate encoded endpoints and preserves pending state", async (t) => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const response = { status: "pending", agent_id: "member/a", agent: { id: "member/a", state: "active" }, scheduling_pending: "sleep", stop_pending: false };
  t.mock.method(globalThis, "fetch", async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(response), { status: 202 });
  });
  const client = new RestClient();
  const deadline = "2030-01-01T00:00:00.000Z";
  const results = await Promise.all([
    client.wakeAgent("member/a"), client.waitAgent("member/a"),
    client.sleepAgent("member/a", deadline), client.terminateAgent("member/a"),
  ]);
  assert.deepEqual(calls.map((call) => call.url), ["wake", "wait", "sleep", "terminate"].map((action) => `/admin/agents/member%2Fa/${action}`));
  assert.ok(calls.every((call) => call.init?.method === "POST"));
  assert.deepEqual(JSON.parse(String(calls[2]?.init?.body)), { wake_at: deadline });
  for (const result of results) {
    assert.equal(result.agent.state, "active");
    assert.equal(result.scheduling_pending, "sleep");
    assert.match(schedulingNotice("sleep", result), /请求已接受.*当前工作结束/);
    assert.ok(!schedulingNotice("sleep", result).includes("已进入睡眠"));
  }
});

test("completed actions describe mail-driven activity and permanent history retention", () => {
  const result = { status: "success" } as AgentSchedulingResponse;
  assert.match(schedulingNotice("wake", result), /有待处理邮件.*否则进入等待/);
  assert.match(schedulingNotice("wait", result), /立即再次活跃/);
  assert.match(schedulingNotice("terminate", result), /拒收新邮件.*历史记录保留/);
});
