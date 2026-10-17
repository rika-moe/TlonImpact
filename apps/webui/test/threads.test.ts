import assert from "node:assert/strict";
import { test } from "node:test";
import { groupByThread, involvesMe, localPart, mergeMails, threadMatchesFilter } from "../src/lib/threads";
import type { MailItem } from "../src/types";

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
    read: true,
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
  assert.deepEqual(threads[0]?.participants, [AGENT]);
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

test("groupByThread treats mails without a read flag as read (observer payloads)", () => {
  const observed = mail({ id: "m1" });
  delete observed.read;
  const threads = groupByThread([observed], ME);
  assert.equal(threads[0]?.unread, false);
});

test("groupByThread collects participants across from/to/cc", () => {
  const m = mail({
    from_address: AGENT,
    to: [ME, "pm-001@tlon.internal"],
    cc: ["gu-yanxi@tlon.internal", AGENT],
  });
  const threads = groupByThread([m], ME);
  assert.deepEqual(threads[0]?.participants, [AGENT, "pm-001@tlon.internal", "gu-yanxi@tlon.internal"]);
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
  assert.equal(merged[0]?.content, "same mail, second recipient event");
});

test("stable root title survives newer replies and incomplete history is explicit", () => {
  const root = mail({ id: "t1", subject: "Project launch", content: "original" });
  const reply = mail({ id: "r1", subject: "Re: changed", content: "changed topic", timestamp: "2026-07-30T09:00:00Z", read: false });
  const incomplete = groupByThread([reply], ME)[0]!;
  assert.equal(incomplete.rootLoaded, false);
  assert.equal(incomplete.title, "Re: changed");
  const complete = groupByThread([reply, root], ME)[0]!;
  assert.equal(complete.rootLoaded, true);
  assert.equal(complete.title, "Project launch");
  assert.equal(complete.unreadCount, 1);
  assert.equal(groupByThread([mail({ id: "t1", content: " hello\n  world " })], ME)[0]?.title, "hello world");
});

test("same participants and subjects never merge distinct thread ids", () => {
  assert.equal(groupByThread([mail({ id: "t1", thread_id: "t1" }), mail({ id: "t2", thread_id: "t2" })], ME).length, 2);
  const sameName = mail({ to: [ME, "xie-yunshu@external.example"] });
  assert.deepEqual(groupByThread([sameName], ME)[0]?.participants, [AGENT, "xie-yunshu@external.example"]);
});

test("sorting compares actual instants, with deterministic equal and invalid timestamp ordering", () => {
  const mails = [
    mail({ id: "late", timestamp: "2026-07-29T08:00:00Z" }),
    mail({ id: "early", timestamp: "2026-07-29T10:00:00+08:00" }),
    mail({ id: "bad", timestamp: "invalid" }),
    mail({ id: "late2", timestamp: "2026-07-29T08:00:00Z" }),
  ];
  assert.deepEqual(groupByThread(mails, ME)[0]?.mails.map((item) => item.id), ["bad", "early", "late", "late2"]);
  const separate = mails.map((item) => ({ ...item, thread_id: item.id }));
  assert.deepEqual(groupByThread(separate, ME).map((thread) => thread.threadId), ["late2", "late", "early", "bad"]);
  assert.deepEqual(groupByThread([...separate].reverse(), ME).map((thread) => thread.threadId), ["late2", "late", "early", "bad"]);
});

test("merge updates authoritative read state but missing event read preserves it", () => {
  const original = mail({ read: false });
  const updated = mergeMails([original], [mail({ read: true })]);
  assert.equal(updated[0]?.read, true);
  const event = mail({ content: "latest" });
  delete event.read;
  assert.equal(mergeMails(updated, [event])[0]?.read, true);
  assert.equal(original.read, false);
  assert.equal(mergeMails(updated, [mail({ read: false })])[0]?.read, false);
});

test("filter searches all loaded messages, subjects and complete participant addresses", () => {
  const thread = groupByThread([
    mail({ id: "t1", content: "first body needle", subject: "original subject" }),
    mail({ id: "reply", content: "newer unrelated", timestamp: "2026-07-30T09:00:00Z" }),
  ], ME)[0]!;
  assert.ok(threadMatchesFilter(thread, " NEEDLE "));
  assert.ok(threadMatchesFilter(thread, "original subject"));
  assert.ok(threadMatchesFilter(thread, AGENT));
  assert.ok(threadMatchesFilter(thread, ME));
  assert.ok(!threadMatchesFilter(thread, "nonexistent"));
});
