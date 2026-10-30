import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient } from "../src/client/rest";
import type { ModelSetupRequest, ModelSetupStatus } from "../src/client/rest";
import { ModelSetupController } from "../src/lib/model-setup";

const required: ModelSetupStatus = { enabled: true, required: true, providers: ["openai"], initial_member_ids: ["m-a"], members: [{ id: "m-a", name: "Alice", model: "deepseek/deepseek-chat" }] };
const configured: ModelSetupStatus = { ...required, required: false, pending_members: [] };
const body: ModelSetupRequest = { provider: "openai", model: "openai/gpt-4o-mini", api_key: "secret-test-key", scope: "initial", agent_ids: [] };

test("portable setup stays gated on missing credentials or failed validation and never stores key in controller", async () => {
  let fail = true;
  const controller = new ModelSetupController({
    modelSetupStatus: async () => required,
    configureModels: async () => { if (fail) throw new Error("连接验证失败"); return configured; },
  });
  await controller.load();
  assert.equal(controller.getSnapshot().status?.required, true);
  assert.equal(await controller.save(body), false);
  assert.equal(controller.getSnapshot().status?.required, true);
  assert.equal(controller.getSnapshot().saved, false);
  assert.equal(controller.getSnapshot().error, "连接验证失败");
  fail = false;
  assert.equal(await controller.save(body), true);
  assert.equal(controller.getSnapshot().status?.required, false);
  assert.equal(JSON.stringify(controller.getSnapshot()).includes(body.api_key), false);
});

test("developer CLI bypasses setup while unavailable status remains retryable", async () => {
  let fail = true;
  const controller = new ModelSetupController({
    modelSetupStatus: async () => { if (fail) throw new Error("network"); return { enabled: false }; },
    configureModels: async () => configured,
  });
  await controller.load();
  assert.equal(controller.getSnapshot().status, null);
  assert.equal(controller.getSnapshot().busy, false);
  fail = false;
  await controller.load();
  assert.equal(controller.getSnapshot().status?.enabled, false);
  assert.equal(controller.getSnapshot().error, null);
});

test("duplicate submissions cannot race and pending model application is retained", async () => {
  let finish!: (value: ModelSetupStatus) => void;
  let writes = 0;
  const controller = new ModelSetupController({
    modelSetupStatus: async () => required,
    configureModels: async () => { writes++; return new Promise((resolve) => { finish = resolve; }); },
  });
  await controller.load();
  const saving = controller.save(body);
  assert.equal(await controller.save(body), false);
  finish({ ...configured, pending_members: ["m-a"] });
  assert.equal(await saving, true);
  assert.equal(writes, 1);
  assert.deepEqual(controller.getSnapshot().status?.pending_members, ["m-a"]);
});

test("setup REST uses the existing browser boundary and explicit selected scope", async (t) => {
  const selected: ModelSetupRequest = { ...body, scope: "selected", agent_ids: [] };
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    assert.equal(url, "/admin/llm/setup");
    assert.equal(new Headers(init.headers).get("X-Tlon-WebUI"), "1");
    if (init.method === "POST") assert.deepEqual(JSON.parse(String(init.body)), selected);
    return new Response(JSON.stringify(configured));
  });
  const rest = new RestClient();
  assert.deepEqual(await rest.modelSetupStatus(), configured);
  assert.deepEqual(await rest.configureModels(selected), configured);
});
