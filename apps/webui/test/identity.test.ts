import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient, RestError } from "../src/client/rest";
import { IdentityController } from "../src/lib/identity";
import { devOriginAllowed } from "../devProxy";

const empty = { username: null, address: null };
const existing = { username: "alice", address: "alice@tlon.internal" };

test("first visit loads empty identity, saves once and subsequent visits reuse instance identity", async (t) => {
  let saved = empty as typeof empty | typeof existing;
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    assert.equal(url, "/identity");
    assert.equal(new Headers(init.headers).get("X-Tlon-WebUI"), "1");
    calls.push(init.method ?? "GET");
    if (init.method === "POST") {
      assert.deepEqual(JSON.parse(String(init.body)), { username: "alice" });
      saved = existing;
    }
    return new Response(JSON.stringify(saved));
  });
  const first = new IdentityController(new RestClient());
  await first.load();
  assert.deepEqual(first.getSnapshot().identity, empty);
  await first.initialize(" alice ");
  assert.deepEqual(first.getSnapshot().identity, existing);
  const anotherBrowser = new IdentityController(new RestClient());
  await anotherBrowser.load();
  assert.deepEqual(anotherBrowser.getSnapshot().identity, existing);
  assert.deepEqual(calls, ["GET", "POST", "GET"]);
});

test("load and initialization failures remain retryable", async () => {
  let fail = true;
  const controller = new IdentityController({
    identity: async () => { if (fail) throw new Error("offline"); return empty; },
    initializeIdentity: async () => { if (fail) throw new RestError("invalid name", 400); return existing; },
  });
  await controller.load();
  assert.equal(controller.getSnapshot().error, "offline");
  assert.equal(controller.getSnapshot().busy, false);
  fail = false;
  await controller.load();
  fail = true;
  await controller.initialize("invalid");
  assert.equal(controller.getSnapshot().error, "invalid name");
  assert.deepEqual(controller.getSnapshot().identity, empty);
  fail = false;
  await controller.initialize("alice");
  assert.deepEqual(controller.getSnapshot().identity, existing);
  assert.equal(controller.getSnapshot().error, null);
});

test("a second tab recovers the already initialized identity after conflict", async () => {
  let reads = 0;
  const controller = new IdentityController({
    identity: async () => ++reads === 1 ? empty : existing,
    initializeIdentity: async () => { throw new RestError("already initialized", 409); },
  });
  await controller.load();
  await controller.initialize("bob");
  assert.deepEqual(controller.getSnapshot().identity, existing);
  assert.equal(controller.getSnapshot().error, null);
});

test("development proxy checks exact incoming origin before rewriting it", () => {
  assert.equal(devOriginAllowed("http://localhost:5173", "localhost:5173"), true);
  assert.equal(devOriginAllowed("http://evil.example", "localhost:5173"), false);
  assert.equal(devOriginAllowed("http://localhost:5174", "localhost:5173"), false);
  assert.equal(devOriginAllowed("null", "localhost:5173"), false);
  assert.equal(devOriginAllowed("http://localhost:5173", undefined), false);
  assert.equal(devOriginAllowed(undefined, "localhost:5173"), true);
});

test("a stale startup response cannot replace a newly initialized identity", async () => {
  let finishOld!: (identity: typeof empty) => void;
  let reads = 0;
  const controller = new IdentityController({
    identity: async () => ++reads === 1
      ? new Promise<typeof empty>((resolve) => { finishOld = resolve; }) : empty,
    initializeIdentity: async () => existing,
  });
  const oldLoad = controller.load();
  await controller.load();
  await controller.initialize("alice");
  finishOld(empty);
  await oldLoad;
  assert.deepEqual(controller.getSnapshot().identity, existing);
});

test("a conflicting member name preserves server error and permits choosing another name", async () => {
  let conflict = true;
  const controller = new IdentityController({
    identity: async () => empty,
    initializeIdentity: async () => {
      if (conflict) throw new RestError("Username conflicts with an existing member", 409);
      return existing;
    },
  });
  await controller.load();
  await controller.initialize("existing-agent");
  assert.deepEqual(controller.getSnapshot().identity, empty);
  assert.equal(controller.getSnapshot().error, "Username conflicts with an existing member");
  conflict = false;
  await controller.initialize("alice");
  assert.deepEqual(controller.getSnapshot().identity, existing);
  assert.equal(controller.getSnapshot().error, null);
});
