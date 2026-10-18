import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient, type AgentConfig } from "../src/client/rest";
import { buildAgentUpdate, PERSONA_FIELDS } from "../src/lib/agentConfig";
const config: AgentConfig = {
  status: "success", agent_id: "m-a",
  persona: { identity: "我是研究者\n", profile: "重视证据\n", behavior: "报告附来源\n" },
  llm: { model_name: "provider/model" },
};
test("config read loads full persona files and model from the human config endpoint", async (t) => {
  t.mock.method(globalThis, "fetch", async (url: unknown) => {
    assert.equal(String(url), "/admin/config/agents/m-a");
    return new Response(JSON.stringify(config));
  });
  assert.deepEqual(await new RestClient().getAgentConfig("m-a"), config);
  assert.deepEqual(PERSONA_FIELDS.map(({ name }) => name), ["IDENTITY.md", "SOUL.md", "RULES.md"]);
});
test("one edited file preserves whitespace and omits untouched files and model", () => {
  assert.deepEqual(buildAgentUpdate(config, { ...config.persona, profile: "  保持怀疑\n" }, config.llm.model_name), {
    persona: { profile: "  保持怀疑\n" },
  });
  assert.deepEqual(buildAgentUpdate(config, config.persona, config.llm.model_name), {});
});
test("clearing each persona file explicitly PATCHes an empty string", async (t) => {
  const bodies: unknown[] = [];
  t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
    bodies.push(JSON.parse(String(init.body)));
    return new Response("{}");
  });
  for (const { key } of PERSONA_FIELDS) {
    const update = buildAgentUpdate(config, { ...config.persona, [key]: "" }, config.llm.model_name);
    await new RestClient().updateAgent(config.agent_id, update);
    assert.deepEqual(bodies.at(-1), { persona: { [key]: "" } });
  }
});
test("failed reads remain errors and cannot produce a save", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response('{"detail":"not found"}', { status: 404 }));
  await assert.rejects(new RestClient().getAgentConfig("missing"));
  assert.throws(() => buildAgentUpdate(null, config.persona, config.llm.model_name), /配置尚未加载/);
});
test("model changes require a value; existing absent models permit persona edits", () => {
  assert.deepEqual(buildAgentUpdate(config, config.persona, " next/model "), { llm: { model_name: "next/model" } });
  assert.throws(() => buildAgentUpdate(config, config.persona, ""), /MODEL/);
  assert.deepEqual(buildAgentUpdate({ ...config, llm: { model_name: "" } }, { ...config.persona, identity: "新身份" }, ""), {
    persona: { identity: "新身份" },
  });
});
