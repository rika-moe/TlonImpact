import assert from "node:assert/strict";
import { test } from "node:test";
import { groupByThread, involvesMe, localPart, mergeMails } from "../src/mail/threads.js";
import type { MailItem } from "../src/types.js";

const ME = "alice@tlon.internal";
const AGENT = "xie-yunshu@tlon.internal";

function mail(overrides: Partial<MailItem>): MailItem {
  return {
    id: "m1",
    from_address: AGENT,
    to: [ME],
    cc: [],
    subject: null,
    content: "hi",
    in_reply_to: null,
    thread_id: "t1",
    forward_of: null,
    timestamp: "2026-07-29T09:00:00+00:00",
    ...overrides,
  };
}

test("localPart strips the domain and tolerates bare names", () => {
  assert.equal(localPart("xie-yunshu@tlon.internal"), "xie-yunshu");
  assert.equal(localPart("谢云舒"), "谢云舒");
  assert.equal(localPart("a@b@c"), "a");
});

test("involvesMe covers sender, to and cc", () => {
  assert.ok(involvesMe(mail({ from_address: ME, to: [AGENT] }), ME));
  assert.ok(involvesMe(mail({ to: [ME] }), ME));
  assert.ok(involvesMe(mail({ to: [AGENT], cc: [ME] }), ME));
  assert.ok(!involvesMe(mail({ to: [AGENT] }), ME));
});

test("groupByThread groups by thread_id and sorts mails chronologically", () => {
  const older = mail({ id: "m1", timestamp: "2026-07-29T09:00:00+00:00" });
  const newer = mail({
    id: "m2",
    from_address: ME,
    to: [AGENT],
    in_reply_to: "m1",
    timestamp: "2026-07-29T10:00:00+00:00",
  });
  const threads = groupByThread([newer, older], ME);
  assert.equal(threads.length, 1);
  assert.deepEqual(
    threads[0]?.mails.map((m) => m.id),
    ["m1", "m2"],
  );
  assert.equal(threads[0]?.count, 2);
  assert.equal(threads[0]?.latest.id, "m2");
  // Participants exclude my own address, deduplicated.
  assert.deepEqual(threads[0]?.participants, ["xie-yunshu"]);
});

test("groupByThread orders threads by latest mail, newest first", () => {
  const t1 = mail({ id: "m1", thread_id: "t1", timestamp: "2026-07-29T09:00:00+00:00" });
  const t2 = mail({ id: "m2", thread_id: "t2", timestamp: "2026-07-29T11:00:00+00:00" });
  const threads = groupByThread([t1, t2], ME);
  assert.deepEqual(
    threads.map((t) => t.threadId),
    ["t2", "t1"],
  );
});

test("groupByThread falls back to the mail id when thread_id is empty", () => {
  const orphan = mail({ id: "solo", thread_id: "" });
  const threads = groupByThread([orphan], ME);
  assert.equal(threads[0]?.threadId, "solo");
});

test("groupByThread collects participants across from/to/cc", () => {
  const m = mail({
    from_address: AGENT,
    to: [ME, "pm-001@tlon.internal"],
    cc: ["gu-yanxi@tlon.internal", AGENT],
  });
  const threads = groupByThread([m], ME);
  assert.deepEqual(threads[0]?.participants, ["xie-yunshu", "pm-001", "gu-yanxi"]);
});

test("mergeMails deduplicates by mail id", () => {
  const m = mail({ id: "m1" });
  const dup = mail({ id: "m1", content: "same mail, second recipient event" });
  const other = mail({ id: "m2" });
  const merged = mergeMails([m], [dup, other]);
  assert.deepEqual(
    merged.map((x) => x.id),
    ["m1", "m2"],
  );
  assert.equal(merged[0]?.content, "hi");
});
