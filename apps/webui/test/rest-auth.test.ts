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
  await rest.createPrototype({ name: "test", behavior: "test" });
  await rest.approveCapability("test");
  for (const call of calls) {
    const headers = new Headers(call.headers);
    assert.equal(headers.get("X-Tlon-WebUI"), "1");
    assert.equal(headers.has("Authorization"), false);
  }
});
