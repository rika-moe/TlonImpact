import assert from "node:assert/strict";
import { test } from "node:test";
import { SseClient } from "../src/client/sse";
import { MailboxController } from "../src/lib/mailbox";
import type { MailItem, MailPage } from "../src/types";

const address = "alice@tlon.internal";
function mail(id: string, extra: Partial<MailItem> = {}): MailItem {
  return { id, thread_id: id, from_address: "bob@tlon.internal", to: [address], cc: [], subject: id,
    content: id, in_reply_to: null, forward_of: null, timestamp: "2026-10-17T00:00:00Z", read: false, ...extra };
}
const page = (mails: MailItem[], before: string | null = null): MailPage => ({ mails, has_more: before !== null, next_before: before });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
class Stream extends SseClient {
  ready = false;
  override getStatus() { return { state: this.ready ? "connected" as const : "disconnected" as const, attempt: 0 }; }
  changeStatus(ready: boolean) { this.ready = ready; this.emit("status", this.getStatus()); }
  delivery(value: MailItem) { this.emit("event", { eventType: "mail.delivered", payload: { mail: value }, receivedAt: 0 }); }
}
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));
function setup(load: (before?: string) => Promise<MailPage>, mark = async () => ({ mail_ids: ["a"], read: true })) {
  const sse = new Stream();
  const rest = { getInboxPage: (options: { before?: string } = {}) => load(options.before),
    getMemberMailPage: (_: string, options: { before?: string } = {}) => load(options.before), setMailRead: mark };
  const box = new MailboxController(rest, sse, "human:alice", address, false);
  box.start();
  return { box, sse, rest };
}

test("subscribes before ready snapshot and retains live mail and send acknowledgement", async () => {
  const snapshot = deferred<MailPage>();
  let calls = 0;
  const { box, sse } = setup(() => { calls++; return snapshot.promise; });
  assert.equal(calls, 0);
  sse.changeStatus(true);
  sse.delivery(mail("live", { read: undefined }));
  box.addLocal(mail("sent", { from_address: address, to: ["bob@tlon.internal"] }));
  snapshot.resolve(page([mail("old")]));
  await flush();
  assert.deepEqual(box.getSnapshot().mails.map((m) => m.id), ["live", "sent", "old"]);
  assert.equal(box.getSnapshot().mails[0]?.read, false);
  assert.equal(box.getSnapshot().mails[1]?.read, undefined);
  box.stop();
});

test("reconnect paginates a gap larger than 100 and retains history cursor", async () => {
  const calls: (string | undefined)[] = [];
  let reconnect = false;
  const { box, sse } = setup(async (before) => {
    calls.push(before);
    if (!reconnect) return page([mail("known")], "history");
    if (before === "gap") return page([mail("known"), mail("extra")], "older-gap");
    if (before === "history") return page([mail("historic")]);
    return page(Array.from({ length: 100 }, (_, i) => mail(`new-${i}`)), "gap");
  });
  sse.changeStatus(true); await flush();
  reconnect = true;
  sse.changeStatus(false); sse.changeStatus(true); await flush();
  assert.equal(box.getSnapshot().mails.length, 102);
  assert.deepEqual(calls, [undefined, undefined, "gap"]);
  await box.loadMore();
  assert.equal(calls.at(-1), "history");
  assert.equal(box.getSnapshot().hasMore, false);
  box.stop();
});

test("mutation beats older snapshot including a mail not loaded at mutation time", async () => {
  const snapshot = deferred<MailPage>();
  const { box, sse } = setup(() => snapshot.promise, async () => ({ mail_ids: ["a", "hidden"], read: true }));
  sse.changeStatus(true);
  sse.delivery(mail("a"));
  await box.setRead(true, { thread_id: "a" });
  // The thread mutation also covers copies not loaded yet. A delayed
  // delivery notice must not reset such a copy to unread before its snapshot.
  sse.delivery(mail("hidden", { read: undefined }));
  assert.equal(box.getSnapshot().mails.find((m) => m.id === "hidden")?.read, true);
  snapshot.resolve(page([mail("a"), mail("hidden")])); await flush();
  assert.equal(box.getSnapshot().mails.length, 2);
  assert.ok(box.getSnapshot().mails.every((m) => m.read === true));
  sse.delivery(mail("a", { read: undefined }));
  assert.equal(box.getSnapshot().mails[0]?.read, true);
  box.stop();
});

test("later authoritative snapshot can update read state", async () => {
  let read = false;
  const { box, sse } = setup(async () => page([mail("a", { read })]));
  sse.changeStatus(true); await flush();
  await box.setRead(true, { mail_id: "a" });
  sse.changeStatus(false); sse.changeStatus(true); await flush();
  assert.equal(box.getSnapshot().mails[0]?.read, false);
  read = true;
  sse.changeStatus(false); sse.changeStatus(true); await flush();
  assert.equal(box.getSnapshot().mails[0]?.read, true);
  box.stop();
});

test("duplicate delivery during refresh cannot block authoritative read state", async () => {
  const snapshot = deferred<MailPage>();
  let calls = 0;
  const { box, sse } = setup(() => ++calls === 1
    ? Promise.resolve(page([mail("a", { read: false })])) : snapshot.promise);
  sse.changeStatus(true); await flush();
  const refresh = box.refresh();
  sse.delivery(mail("a", { read: undefined, content: "event" }));
  snapshot.resolve(page([mail("a", { read: true, content: "snapshot" })]));
  await refresh;
  assert.equal(box.getSnapshot().mails[0]?.read, true);
  assert.equal(box.getSnapshot().mails[0]?.content, "snapshot");
  box.stop();
});

test("old mailbox requests, sends and mutation acknowledgements cannot publish after stop", async () => {
  const snapshot = deferred<MailPage>();
  const mutation = deferred<{ mail_ids: string[]; read: boolean }>();
  const { box, sse, rest } = setup(() => snapshot.promise, () => mutation.promise);
  sse.changeStatus(true);
  const pendingMark = box.setRead(true, { mail_id: "a" });
  box.stop();
  const observer = new MailboxController(rest, sse, "bob", "bob@tlon.internal", true);
  observer.start();
  snapshot.resolve(page([mail("a")])); mutation.resolve({ mail_ids: ["a"], read: true });
  box.addLocal(mail("sent")); await pendingMark; await flush();
  assert.deepEqual(box.getSnapshot().mails, []);
  assert.equal(observer.getSnapshot().mails[0]?.read, undefined);
  observer.stop();
});

test("strict effect restart ignores the previous generation snapshot", async () => {
  const old = deferred<MailPage>(); const fresh = deferred<MailPage>();
  let calls = 0;
  const { box, sse } = setup(() => (++calls === 1 ? old.promise : fresh.promise));
  sse.changeStatus(true); box.stop(); box.start();
  fresh.resolve(page([mail("fresh")])); await flush();
  old.resolve(page([mail("old")])); await flush();
  assert.deepEqual(box.getSnapshot().mails.map((m) => m.id), ["fresh"]);
  box.stop();
});

test("read requests prevent duplicates and display failures", async () => {
  const mutation = deferred<{ mail_ids: string[]; read: boolean }>(); let calls = 0;
  const { box } = setup(async () => page([]), () => { calls++; return mutation.promise; });
  const first = box.setRead(true, { mail_id: "a" });
  await box.setRead(false, { mail_id: "a" });
  assert.equal(calls, 1);
  mutation.reject(new Error("write failed")); await first;
  assert.match(box.getSnapshot().notice ?? "", /Failed to update read status.*write failed/);
  assert.equal(box.getSnapshot().marking, false);
  box.stop();
});

test("a send acknowledged during outage cannot terminate gap recovery", async () => {
  let reconnect = false;
  const calls: (string | undefined)[] = [];
  const sent = mail("local-send", { from_address: address, to: ["bob@tlon.internal"] });
  const { box, sse } = setup(async (before) => {
    calls.push(before);
    if (!reconnect) return page([mail("baseline")]);
    if (before === "gap") return page([mail("baseline"), ...Array.from({ length: 26 }, (_, i) => mail(`older-${i}`))]);
    return page([...Array.from({ length: 99 }, (_, i) => mail(`new-${i}`)), sent], "gap");
  });
  sse.changeStatus(true); await flush();
  sse.changeStatus(false);
  box.addLocal(sent);
  reconnect = true;
  sse.changeStatus(true); await flush();
  assert.deepEqual(calls, [undefined, undefined, "gap"]);
  assert.equal(box.getSnapshot().mails.length, 127);
  box.stop();
});

test("failed gap recovery does not move the last completed snapshot boundary", async () => {
  let reconnect = false; let fail = true;
  const calls: (string | undefined)[] = [];
  const { box, sse } = setup(async (before) => {
    calls.push(before);
    if (!reconnect) return page([mail("baseline")]);
    if (!before) return page([mail("new")], "gap");
    if (fail) throw new Error("network interrupted");
    return page([mail("baseline"), mail("missing")]);
  });
  sse.changeStatus(true); await flush(); reconnect = true;
  sse.changeStatus(false); sse.changeStatus(true); await flush();
  assert.match(box.getSnapshot().notice ?? "", /Mail sync failed/);
  fail = false;
  sse.changeStatus(false); sse.changeStatus(true); await flush();
  assert.equal(box.getSnapshot().mails.length, 3);
  assert.deepEqual(calls, [undefined, undefined, "gap", undefined, "gap"]);
  box.stop();
});

test("explicit refresh recovers REST failure while SSE stays connected", async () => {
  let failed = true;
  const { box, sse } = setup(async () => {
    if (failed) throw new Error("temporary REST failure");
    return page([mail("recovered")]);
  });
  sse.changeStatus(true); await flush();
  assert.equal(box.getSnapshot().loading, false);
  assert.equal(box.getSnapshot().syncing, false);
  assert.match(box.getSnapshot().notice ?? "", /Mail sync failed/);
  failed = false;
  await box.refresh();
  assert.equal(box.getSnapshot().mails[0]?.id, "recovered");
  box.stop();
});
