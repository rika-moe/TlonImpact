import assert from "node:assert/strict";
import { test } from "node:test";
import type { TestContext } from "node:test";
import { SseClient } from "../src/client/sse";

class Source {
  static instances: Source[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  closed = false;
  constructor(readonly url: string) { Source.instances.push(this); }
  close(): void { this.closed = true; }
}

function sources(t: TestContext): void {
  Source.instances = [];
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "EventSource");
  Object.defineProperty(globalThis, "EventSource", { configurable: true, value: Source });
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "EventSource", descriptor);
    else Reflect.deleteProperty(globalThis, "EventSource");
  });
}

async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 30; i += 1) {
    if (predicate()) return;
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
  assert.fail("expected SSE transition did not occur");
}

test("connected means stream open, not just session creation; reconnect opens a new stream", async (t) => {
  sources(t);
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let sessions = 0;
  t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
    if (init.method === "POST") {
      return new Response(JSON.stringify({ session_id: `s${++sessions}` }));
    }
    return new Response("{}");
  });
  const client = new SseClient();
  t.after(() => client.dispose());
  const connected: string[] = [];
  client.on("status", (status) => {
    if (status.state === "connected") connected.push(String(status.sessionId));
  });
  client.connect();
  await until(() => Source.instances.length === 1);
  assert.equal(client.getStatus().state, "connecting");
  assert.deepEqual(connected, []);
  const first = Source.instances[0]!;
  first.onopen?.();
  assert.deepEqual(connected, ["s1"]);
  first.onerror?.();
  assert.ok(first.closed);
  assert.equal(client.getStatus().state, "reconnecting");
  t.mock.timers.tick(1000);
  await until(() => Source.instances.length === 2);
  assert.deepEqual(connected, ["s1"]);
  Source.instances[1]!.onopen?.();
  assert.deepEqual(connected, ["s1", "s2"]);
});

test("dispose while session is opening cannot attach a stale stream after reconnect", async (t) => {
  sources(t);
  let finishOld: (response: Response) => void = () => {};
  const old = new Promise<Response>((resolve) => { finishOld = resolve; });
  const deleted: string[] = [];
  let sessions = 0;
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    if (init.method === "POST") {
      sessions += 1;
      return sessions === 1 ? old : new Response(JSON.stringify({ session_id: "new" }));
    }
    if (init.method === "DELETE") deleted.push(String(url));
    return new Response("{}");
  });
  const client = new SseClient();
  t.after(() => client.dispose());
  client.connect();
  client.dispose();
  client.connect();
  await until(() => Source.instances.length === 1);
  finishOld(new Response(JSON.stringify({ session_id: "old" })));
  await until(() => deleted.includes("/sessions/old"));
  assert.equal(Source.instances.length, 1);
  assert.equal(client.getSessionId(), "new");
  assert.ok(Source.instances[0]!.url.includes("new"));
});

test("failed event subscription never reports a connected mailbox", async (t) => {
  sources(t);
  t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
    return init.method === "POST"
      ? new Response(JSON.stringify({ session_id: "s1" }))
      : new Response("{}", { status: init.method === "PATCH" ? 500 : 200 });
  });
  const client = new SseClient();
  t.after(() => client.dispose());
  client.connect();
  await until(() => client.getStatus().state === "reconnecting");
  assert.equal(Source.instances.length, 0);
});
