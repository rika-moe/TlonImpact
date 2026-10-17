import assert from "node:assert/strict";
import { test } from "node:test";
import { RestClient, RestError } from "../src/client/rest";
import type { MailItem } from "../src/types";

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

function mail(id: string): MailItem {
  return {
    id,
    from_address: "gu-yanxi@tlon.internal",
    to: ["alice@tlon.internal"],
    cc: [],
    subject: null,
    content: "hi",
    in_reply_to: null,
    thread_id: "t1",
    forward_of: null,
    timestamp: "2026-10-07T09:00:00+00:00",
    // No read flag: the observer endpoint never carries one.
  };
}

test("getMemberMail builds the observer URL with an encoded member id", async () => {
  const calls = stubFetch(200, { mails: [mail("m1")], count: 1 });
  const rest = new RestClient();
  const mails = await rest.getMemberMail("human:alice");
  assert.equal(calls[0]?.url, "/members/human%3Aalice/mail?limit=100");
  assert.equal(mails.length, 1);
  assert.equal(mails[0]?.read, undefined);
});

test("getMemberMail passes a custom limit and bare agent ids unencoded", async () => {
  const calls = stubFetch(200, { mails: [], count: 0 });
  const rest = new RestClient();
  const mails = await rest.getMemberMail("gu-yanxi", 25);
  assert.equal(calls[0]?.url, "/members/gu-yanxi/mail?limit=25");
  assert.deepEqual(mails, []);
});

test("getMemberMail surfaces the server's 404 as a RestError", async () => {
  stubFetch(404, { error: "Member 'ghost' not found" });
  const rest = new RestClient();
  await assert.rejects(
    () => rest.getMemberMail("ghost"),
    (err: unknown) => {
      assert.ok(err instanceof RestError);
      assert.equal(err.status, 404);
      return true;
    },
  );
});
