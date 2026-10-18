import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StateBadge, SchedulingInfo } from "../src/components/AgentSchedulingState";
import assert from "node:assert/strict";
import { test } from "node:test";
import { stateTone, wakeLabel } from "../src/lib/agentState";
import { RestClient } from "../src/client/rest";

test("four scheduling states have distinct tones", () => {
  assert.equal(stateTone("active"), "cyan");
  assert.equal(stateTone("waiting"), "yellow");
  assert.equal(stateTone("sleeping"), "dim");
  assert.equal(stateTone("dead"), "magenta");
});

test("sleep deadline is shown as a local time", () => {
  const deadline = "2030-01-01T00:00:00Z";
  assert.equal(wakeLabel(deadline), `WAKE AT ${new Date(deadline).toLocaleString()}`);
  assert.equal(wakeLabel(null), "");
});

test("management termination uses a separate endpoint and preserves pending response", async () => {
  let calledUrl = "";
  let method: string | undefined;
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    calledUrl = String(url);
    method = init?.method;
    return new Response(JSON.stringify({ stop_pending: true }), { status: 202 });
  }) as typeof fetch;
  const result = await new RestClient().terminateAgent("member/a");
  assert.equal(calledUrl, "/admin/agents/member%2Fa/terminate");
  assert.equal(method, "POST");
  assert.equal(result.stop_pending, true);
});


test("rendered badges use only four scheduling states", () => {
  for (const state of ["active", "waiting", "sleeping", "dead"] as const) {
    const html = renderToStaticMarkup(createElement(StateBadge, { state }));
    assert.ok(html.includes(`>${state}</span>`));
    assert.equal(html.includes("state-badge--active"), state === "active");
  }
});

test("sleep rendering shows the deadline and pending exit remains a separate diagnostic", () => {
  const deadline = "2030-01-01T00:00:00Z";
  const html = renderToStaticMarkup(createElement(SchedulingInfo, {
    status: { state: "sleeping", wake_at: deadline, stop_pending: true },
  }));
  assert.ok(html.includes("WAKE AT"));
  assert.ok(html.includes(new Date(deadline).toLocaleString()));
  assert.ok(html.includes("TERMINATION PENDING"));
  const waiting = renderToStaticMarkup(createElement(SchedulingInfo, {
    status: { state: "waiting", waking: true },
  }));
  assert.ok(waiting.includes("LOADING RUNTIME"));
  assert.equal(waiting.includes("WAKE AT"), false);
});
