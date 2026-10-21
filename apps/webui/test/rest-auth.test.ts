import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient } from "../src/client/rest";

test("REST reads and each write family use the browser boundary without bearer credentials", async (t) => {
  const calls: RequestInit[] = [];
  t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
    calls.push(init);
    return new Response("{}");
  });
  const rest = new RestClient();
  await rest.listPrototypes();
  await rest.deleteAgent("alice");
  await rest.createPrototype({ display_name: "测试行为", behavior: "test" });
  for (const call of calls) {
    const headers = new Headers(call.headers);
    assert.equal(headers.get("X-Tlon-WebUI"), "1");
    assert.equal(headers.has("Authorization"), false);
  }
});

test("creation submits behavior labels and member display names without technical IDs", async (t) => {
  const requests: { url: string; body: Record<string, unknown> }[] = [];
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    requests.push({ url: String(url), body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify({
      status: "success", agent_id: "m-123", prototype: { name: "p-123" },
    }));
  });
  const rest = new RestClient();
  await rest.createPrototype({ display_name: "长预算工具迭代", behavior: "spec" });
  await rest.createAgent({ prototype: "p-123", name: "开发工程师", model: "provider/model" });
  assert.deepEqual(requests[0]?.body, { display_name: "长预算工具迭代", behavior: "spec" });
  assert.deepEqual(requests[1]?.body, { prototype: "p-123", name: "开发工程师", model: "provider/model" });
  assert.equal("name" in requests[0]!.body, false);
  assert.equal("agent_id" in requests[1]!.body, false);
});
