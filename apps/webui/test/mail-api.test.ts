import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient, RestError } from "../src/client/rest";

test("mail page calls encode ownership and pagination cursors", async (t) => {
  const calls: string[] = [];
  const page = { mails: [], has_more: true, next_before: "old/mail" };
  t.mock.method(globalThis, "fetch", async (url: unknown) => {
    calls.push(String(url));
    return new Response(JSON.stringify(page));
  });
  const client = new RestClient("", "");
  assert.deepEqual(await client.getInboxPage("a b", { before: "id/+", limit: 25 }), page);
  assert.equal(calls[0], "/inbox?username=a+b&limit=25&before=id%2F%2B");
  assert.deepEqual(await client.getMemberMailPage("human:alice"), page);
  assert.equal(calls[1], "/members/human%3Aalice/mail?limit=100");
});

test("explicit read changes carry the human identity and exact scope", async (t) => {
  const calls: RequestInit[] = [];
  t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
    assert.equal(url, "/inbox/read");
    calls.push(init);
    return new Response(JSON.stringify({ mail_ids: ["m1"], read: false }));
  });
  const client = new RestClient("", "");
  assert.deepEqual(await client.setMailRead("alice", false, { thread_id: "t1" }), {
    mail_ids: ["m1"], read: false,
  });
  assert.equal(calls[0]?.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0]?.body)), {
    username: "alice", read: false, thread_id: "t1",
  });
});

test("read changes surface failure instead of claiming a local success", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(
    JSON.stringify({ error: "No received mail" }), { status: 404 },
  ));
  await assert.rejects(new RestClient("", "").setMailRead("alice", true, { mail_id: "sent" }),
    (error: unknown) => error instanceof RestError && error.status === 404);
});
