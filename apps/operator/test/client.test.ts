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
  assert.deepEqual(defaults, { host: "127.0.0.1", wsPort: 8888, restPort: 8080 });

  const fromEnv = parseArgs([], { TLON_HOST: "example.com", TLON_PORT: "9000" });
  assert.deepEqual(fromEnv, { host: "example.com", wsPort: 9000, restPort: 8080 });

  const fromFlags = parseArgs(["--host", "10.0.0.1", "--port", "9999", "--rest-port=7000"], {});
  assert.deepEqual(fromFlags, { host: "10.0.0.1", wsPort: 9999, restPort: 7000 });
});
