import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient, RestError } from "../src/client/rest";

test("project administration uses browser boundary and stable member IDs for reviewer grants", async (t) => {
  const calls: { url: unknown; init: RequestInit }[] = [];
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    calls.push({ url, init });
    assert.equal(new Headers(init.headers).get("X-Tlon-WebUI"), "1");
    assert.equal(new Headers(init.headers).has("Authorization"), false);
    return new Response(JSON.stringify({ projects: [], members: [] }));
  });
  const rest = new RestClient();
  await rest.giteaProjects();
  await rest.configureGiteaProject({ name: "example", create: true, default_branch: "main", protected_branches: ["main", "release/**"] });
  await rest.setGiteaReviewer("name #1", "member/id", true);
  await rest.setGiteaReviewer("name #1", "member/id", false);
  assert.equal(calls[0]?.url, "/admin/gitea/projects");
  assert.equal(calls[1]?.init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[1]?.init.body)), { name: "example", create: true, default_branch: "main", protected_branches: ["main", "release/**"] });
  assert.equal(calls[2]?.url, "/admin/gitea/projects/name%20%231/reviewers/member%2Fid");
  assert.equal(calls[2]?.init.method, "PUT");
  assert.deepEqual(JSON.parse(String(calls[2]?.init.body)), { enabled: true });
  assert.deepEqual(JSON.parse(String(calls[3]?.init.body)), { enabled: false });
});

test("unavailable Gitea and refused reviewer grants remain errors", async (t) => {
  t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => new Response(JSON.stringify({ error: "项目权限存在漂移" }), { status: init.method === "PUT" ? 409 : 503 }));
  const rest = new RestClient();
  await assert.rejects(rest.giteaProjects(), (err: unknown) => err instanceof RestError && err.status === 503);
  await assert.rejects(rest.setGiteaReviewer("project", "member", true), (err: unknown) => err instanceof RestError && err.status === 409 && err.message === "项目权限存在漂移");
});
