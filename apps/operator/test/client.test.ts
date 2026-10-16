import assert from "node:assert/strict";
import { test } from "node:test";
import { backoffDelay, SseClient } from "../src/client/sseClient.js";
import { MailSendError, RestClient } from "../src/client/restClient.js";
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

test("RestClient builds the base url", () => {
  const client = new RestClient("localhost", 8080);
  assert.equal(client.baseUrl, "http://localhost:8080");
});

test("parseArgs applies defaults, env and flags", () => {
  const defaults = parseArgs([], {});
  assert.deepEqual(defaults, { host: "127.0.0.1", restPort: 8080, username: "guest" });

  const fromEnv = parseArgs([], { TLON_HOST: "example.com", TLON_REST_PORT: "9000", TLON_USERNAME: "alice" });
  assert.deepEqual(fromEnv, { host: "example.com", restPort: 9000, username: "alice" });

  const fromFlags = parseArgs(
    ["--host", "10.0.0.1", "--rest-port=7000", "--username", "alice"],
    {},
  );
  assert.deepEqual(fromFlags, { host: "10.0.0.1", restPort: 7000, username: "alice" });
});

test("RestClient sendMail posts the body and returns the mail id", async () => {
  const client = new RestClient("localhost", 8080);
  const calls: { url: string; init?: RequestInit }[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ mail_id: "m1", request_id: "request-1", local_recipients: ["xie-yunshu"], gateways: [], message: "已入箱" }), { status: 202 });
  }) as typeof fetch;
  try {
    const id = await client.sendMail("alice", {
      request_id: "request-1",
      to: ["谢云舒"],
      cc: ["pm-001"],
      subject: "Re: 周报",
      content: "hello",
      in_reply_to: "m0",
      thread_id: "t0",
    });
    assert.equal(id.mail_id, "m1");
    assert.equal(id.message, "已入箱");
    assert.equal(calls[0]?.url, "http://localhost:8080/messages");
    assert.equal(calls[0]?.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
      username: "alice",
      request_id: "request-1",
      to: ["谢云舒"],
      cc: ["pm-001"],
      subject: "Re: 周报",
      content: "hello",
      in_reply_to: "m0",
      thread_id: "t0",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("RestClient sendMail surfaces the server reason on 4xx", async () => {
  const client = new RestClient("localhost", 8080);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ error: "没有可送达的收件人", reason: "recipient_unresolved" }), {
      status: 400,
    })) as typeof fetch;
  try {
    await assert.rejects(
      () => client.sendMail("alice", { to: ["nobody"], content: "hi" }),
      (err: unknown) => {
        assert.ok(err instanceof MailSendError);
        assert.match(err.message, /^没有可送达的收件人 \(request_id: .+\)$/);
        assert.ok(err.requestId);
        assert.equal(err.reason, "recipient_unresolved");
        assert.equal(err.status, 400);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
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

// ---------------------------------------------------------------------------
// SseClient
// ---------------------------------------------------------------------------

type FetchCall = { url: string; init?: RequestInit };

/** A fetch stub that serves POST /sessions and a controllable SSE stream. */
function stubFetch(options?: { failSessions?: number }) {
  const calls: FetchCall[] = [];
  let streamController: ReadableStreamDefaultController<string> | null = null;
  let failures = options?.failSessions ?? 0;
  const originalFetch = globalThis.fetch;

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.endsWith("/sessions") && init?.method === "POST") {
      if (failures > 0) {
        failures -= 1;
        throw new Error("connection refused");
      }
      return new Response(
        JSON.stringify({
          session_id: "tui-0001",
          username: "alice",
          client_type: "tui",
          mode: "chat",
          filters: {},
          participants: [{ id: "xie-yunshu", name: "谢云舒", type: "agent" }],
          agents: [{ id: "xie-yunshu", state: "running" }],
        }),
        { status: 201 },
      );
    }
    if (url.includes("/events")) {
      const stream = new ReadableStream<string>({
        start(controller) {
          streamController = controller;
        },
      });
      return new Response(
        stream.pipeThrough(new TextEncoderStream()),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    if (init?.method === "PATCH") {
      return new Response(JSON.stringify({ session_id: "tui-0001", mode: "event" }), { status: 200 });
    }
    if (init?.method === "DELETE") {
      return new Response(JSON.stringify({ status: "disconnected" }), { status: 200 });
    }
    return new Response("not found", { status: 404 });
  }) as typeof fetch;

  return {
    calls,
    restore: () => {
      globalThis.fetch = originalFetch;
    },
    /** Push raw text into the open SSE stream. */
    push: (chunk: string) => streamController?.enqueue(chunk),
    hasStream: () => streamController !== null,
  };
}

function nextTick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 10));
}

async function waitFor(predicate: () => boolean, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("condition not met in time");
    await nextTick();
  }
}

test("SseClient builds the base url and reports initial status", () => {
  const client = new SseClient({ host: "127.0.0.1", port: 8080 });
  assert.equal(client.baseUrl, "http://127.0.0.1:8080");
  assert.equal(client.getStatus().state, "disconnected");
  client.dispose();
});

test("SseClient opens a session, emits the snapshot and streams events", async () => {
  const stub = stubFetch();
  const client = new SseClient({ host: "127.0.0.1", port: 8080, username: "alice" });
  const events: unknown[] = [];
  const agents: unknown[] = [];
  const participants: unknown[] = [];
  const statuses: string[] = [];
  client.on("event", (ev: unknown) => events.push(ev));
  client.on("agents", (list: unknown) => agents.push(list));
  client.on("participants", (msg: unknown) => participants.push(msg));
  client.on("status", (s: { state: string }) => statuses.push(s.state));

  try {
    client.connect();
    await waitFor(() => client.getStatus().state === "connected" && stub.hasStream());

    // POST /sessions carried the v2 declaration
    const create = stub.calls[0];
    assert.equal(create?.url, "http://127.0.0.1:8080/sessions");
    assert.deepEqual(JSON.parse(String(create?.init?.body)), {
      username: "alice",
      client_type: "tui",
      mode: "chat",
      filters: {},
      protocol_version: "2.0.0",
    });
    // GET /events followed with the new session id
    const stream = stub.calls[1];
    assert.equal(stream?.url, "http://127.0.0.1:8080/events?session_id=tui-0001");
    assert.equal(client.getSessionId(), "tui-0001");

    // Snapshot emitted as agents/participants events
    assert.deepEqual(agents, [[{ id: "xie-yunshu", state: "running" }]]);
    assert.deepEqual(participants, [
      { type: "participants", participants: [{ id: "xie-yunshu", name: "谢云舒", type: "agent" }] },
    ]);

    // Heartbeat comments are ignored; event frames are dispatched
    stub.push(": ping\n\n");
    stub.push(
      `data: ${JSON.stringify({
        type: "event",
        event_type: "mail.delivered",
        payload: { mail: { id: "m1" } },
        source: "mail_delivery",
        trace_id: "t-1",
      })}\n\n`,
    );
    await waitFor(() => events.length === 1);
    const ev = events[0] as Record<string, unknown>;
    assert.equal(ev["eventType"], "mail.delivered");
    assert.deepEqual(ev["payload"], { mail: { id: "m1" } });
    assert.equal(ev["source"], "mail_delivery");
    assert.equal(ev["traceId"], "t-1");
    assert.equal(typeof ev["receivedAt"], "number");
  } finally {
    client.dispose();
    stub.restore();
  }
  assert.ok(statuses.includes("connecting"));
  assert.ok(statuses.includes("connected"));
});

test("SseClient setMode PATCHes the session and emits mode_changed", async () => {
  const stub = stubFetch();
  const client = new SseClient({ host: "127.0.0.1", port: 8080 });
  const modeChanges: unknown[] = [];
  client.on("mode_changed", (msg: unknown) => modeChanges.push(msg));

  try {
    client.connect();
    await waitFor(() => client.getStatus().state === "connected" && stub.hasStream());

    const ok = await client.setMode("event");
    assert.equal(ok, true);
    const patch = stub.calls.find((call) => call.init?.method === "PATCH");
    assert.equal(patch?.url, "http://127.0.0.1:8080/sessions/tui-0001");
    assert.deepEqual(JSON.parse(String(patch?.init?.body)), { mode: "event" });
    assert.deepEqual(modeChanges, [{ type: "mode_changed", mode: "event" }]);
  } finally {
    client.dispose();
    stub.restore();
  }
});

test("SseClient setMode returns false without a session", async () => {
  const client = new SseClient({ host: "127.0.0.1", port: 8080 });
  assert.equal(await client.setMode("event"), false);
  client.dispose();
});

test("SseClient dispose DELETEs the session", async () => {
  const stub = stubFetch();
  const client = new SseClient({ host: "127.0.0.1", port: 8080 });
  try {
    client.connect();
    await waitFor(() => client.getStatus().state === "connected" && stub.hasStream());
  } finally {
    client.dispose();
  }
  await waitFor(() => stub.calls.some((call) => call.init?.method === "DELETE"));
  const del = stub.calls.find((call) => call.init?.method === "DELETE");
  assert.equal(del?.url, "http://127.0.0.1:8080/sessions/tui-0001");
  assert.equal(client.getStatus().state, "disconnected");
  stub.restore();
});

test("SseClient reconnects with a fresh session after a failure", async () => {
  const stub = stubFetch({ failSessions: 1 });
  const client = new SseClient({ host: "127.0.0.1", port: 8080 });
  const statuses: string[] = [];
  client.on("status", (s: { state: string }) => statuses.push(s.state));

  try {
    client.connect();
    // First POST /sessions fails -> reconnecting; the retry (1s backoff)
    // succeeds and the stream opens.
    await waitFor(() => client.getStatus().state === "connected" && stub.hasStream(), 5_000);
    const posts = stub.calls.filter((call) => call.url.endsWith("/sessions") && call.init?.method === "POST");
    assert.equal(posts.length, 2);
  } finally {
    client.dispose();
    stub.restore();
  }
  assert.ok(statuses.includes("reconnecting"));
});
