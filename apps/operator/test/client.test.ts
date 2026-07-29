import assert from "node:assert/strict";
import { test } from "node:test";
import { backoffDelay, WsClient } from "../src/client/wsClient.js";
import { RestClient } from "../src/client/restClient.js";
import { parseArgs } from "../src/args.js";

test("backoffDelay grows exponentially and caps at max", () => {
  assert.equal(backoffDelay(0), 1_000);
  assert.equal(backoffDelay(1), 2_000);
  assert.equal(backoffDelay(2), 4_000);
  assert.equal(backoffDelay(10), 30_000);
  assert.equal(backoffDelay(100), 30_000);
});

test("backoffDelay respects custom base/max", () => {
  assert.equal(backoffDelay(3, 100, 500), 500);
  assert.equal(backoffDelay(1, 100, 500), 200);
});

test("WsClient builds the /ws url and reports initial status", () => {
  const client = new WsClient({ host: "127.0.0.1", port: 8888 });
  assert.equal(client.url, "ws://127.0.0.1:8888/ws");
  assert.equal(client.getStatus().state, "disconnected");
  client.dispose();
});

test("WsClient emits reconnecting status when the server is unreachable", async () => {
  const client = new WsClient({ host: "127.0.0.1", port: 1 });
  const states: string[] = [];
  client.on("status", (s: { state: string }) => states.push(s.state));
  client.connect();
  await new Promise((resolve) => setTimeout(resolve, 1_500));
  client.dispose();
  assert.ok(states.includes("connecting"));
  assert.ok(states.includes("reconnecting"));
});

test("RestClient builds the base url", () => {
  const client = new RestClient("localhost", 8080);
  assert.equal(client.baseUrl, "http://localhost:8080");
});

test("parseArgs applies defaults, env and flags", () => {
  const defaults = parseArgs([], {});
  assert.deepEqual(defaults, { host: "127.0.0.1", wsPort: 8888, restPort: 8080, username: "guest" });

  const fromEnv = parseArgs([], { TLON_HOST: "example.com", TLON_PORT: "9000", TLON_USERNAME: "alice" });
  assert.deepEqual(fromEnv, { host: "example.com", wsPort: 9000, restPort: 8080, username: "alice" });

  const fromFlags = parseArgs(
    ["--host", "10.0.0.1", "--port", "9999", "--rest-port=7000", "--username", "alice"],
    {},
  );
  assert.deepEqual(fromFlags, { host: "10.0.0.1", wsPort: 9999, restPort: 7000, username: "alice" });
});

test("WsClient puts the username into the auth payload", () => {
  const withName = new WsClient({ host: "127.0.0.1", port: 8888, username: "alice" });
  const auth = (withName as unknown as { authPayload: Record<string, unknown> }).authPayload;
  assert.equal(auth["username"], "alice");
  withName.dispose();

  const withoutName = new WsClient({ host: "127.0.0.1", port: 8888 });
  const bare = (withoutName as unknown as { authPayload: Record<string, unknown> }).authPayload;
  assert.ok(!("username" in bare));
  withoutName.dispose();
});

test("WsClient sendMail builds a message frame with optional fields", () => {
  const client = new WsClient({ host: "127.0.0.1", port: 8888 });
  const sent: Record<string, unknown>[] = [];
  client.send = (msg: Record<string, unknown>) => {
    sent.push(msg);
    return true;
  };

  client.sendMail({ to: ["谢云舒"], content: "hello" });
  assert.deepEqual(sent[0], { type: "message", content: "hello", to: ["谢云舒"] });

  client.sendMail({
    to: ["xie-yunshu"],
    cc: ["pm-001"],
    subject: "Re: 周报",
    content: "ok",
    in_reply_to: "m1",
    thread_id: "t1",
  });
  assert.deepEqual(sent[1], {
    type: "message",
    content: "ok",
    to: ["xie-yunshu"],
    cc: ["pm-001"],
    subject: "Re: 周报",
    in_reply_to: "m1",
    thread_id: "t1",
  });
  client.dispose();
});

test("RestClient getInbox builds the inbox url and returns mails", async () => {
  const client = new RestClient("localhost", 8080);
  const urls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    urls.push(String(input));
    return new Response(JSON.stringify({ mails: [{ id: "m1" }], count: 1 }), { status: 200 });
  }) as typeof fetch;
  try {
    const mails = await client.getInbox("谢云舒", 42);
    assert.equal(urls[0], `http://localhost:8080/inbox?username=${encodeURIComponent("谢云舒")}&limit=42`);
    assert.deepEqual(mails, [{ id: "m1" }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
