import assert from "node:assert/strict";
import { test } from "node:test";
import { ADMIN_TOKEN_KEY, RestClient, RestError } from "../src/client/rest";

/** In-memory localStorage stub (node has no web storage). */
function stubLocalStorage(): Map<string, string> {
  const store = new Map<string, string>();
  globalThis.localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
  return store;
}

type FetchCall = { url: string; init?: RequestInit };

/** Stub global fetch, recording calls and answering with `status`/`body`. */
function stubFetch(status: number, body: unknown = {}): FetchCall[] {
  const calls: FetchCall[] = [];
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
  return calls;
}

function headersOf(call: FetchCall): Record<string, string> {
  return (call.init?.headers ?? {}) as Record<string, string>;
}

test("no token configured: requests carry no Authorization header", async () => {
  stubLocalStorage();
  const calls = stubFetch(200, { agents: [] });
  await new RestClient("", "").listPrototypes();
  assert.equal(headersOf(calls[0])["Authorization"], undefined);
});

test("token configured: every request carries the Bearer header", async () => {
  stubLocalStorage();
  const calls = stubFetch(200, { agents: [] });
  const rest = new RestClient("", "secret-token");
  assert.equal(rest.hasAdminToken, true);
  await rest.listPrototypes();
  assert.equal(headersOf(calls[0])["Authorization"], "Bearer secret-token");
});

test("setAdminToken persists to localStorage; empty string clears it", () => {
  const store = stubLocalStorage();
  const rest = new RestClient("", "");
  rest.setAdminToken("  abc  ");
  assert.equal(store.get(ADMIN_TOKEN_KEY), "abc");
  assert.equal(rest.hasAdminToken, true);
  rest.setAdminToken("");
  assert.equal(store.has(ADMIN_TOKEN_KEY), false);
  assert.equal(rest.hasAdminToken, false);
});

test("constructor picks up the persisted token", () => {
  const store = stubLocalStorage();
  store.set(ADMIN_TOKEN_KEY, "persisted");
  assert.equal(new RestClient().hasAdminToken, true);
});

test("401 errors carry the settings-page hint", async () => {
  stubLocalStorage();
  stubFetch(401, { error: "Admin authentication required", reason: "unauthorized" });
  const rest = new RestClient("", "wrong");
  await assert.rejects(
    () => rest.listAgentConfigs(),
    (err: unknown) => {
      assert.ok(err instanceof RestError);
      assert.equal(err.status, 401);
      assert.match(err.message, /Settings 页配置/);
      return true;
    },
  );
});
