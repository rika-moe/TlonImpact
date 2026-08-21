/**
 * Thread aggregation for the mail view (pure functions, unit-tested).
 *
 * Copied from apps/operator/src/mail/threads.ts (import paths adjusted).
 * Mails arrive from `GET /inbox` (chronological) and from live
 * `mail.delivered` events; here they are grouped into conversation
 * threads keyed by `thread_id`.
 */

import type { MailItem, ThreadGroup } from "../types";

/** The local part of a mail address ("xie-yunshu@tlon.internal" -> "xie-yunshu"). */
export function localPart(address: string): string {
  const at = address.indexOf("@");
  return at === -1 ? address : address.slice(0, at);
}

/** Whether the mail is from me or addressed to me (to/cc). */
export function involvesMe(mail: MailItem, myAddress: string): boolean {
  return mail.from_address === myAddress || mail.to.includes(myAddress) || mail.cc.includes(myAddress);
}

function byTimestampAsc(a: MailItem, b: MailItem): number {
  return a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
}

/** Every distinct participant address of a mail (sender + to + cc). */
function allAddresses(mail: MailItem): string[] {
  return [mail.from_address, ...mail.to, ...mail.cc];
}

/**
 * Group mails into threads keyed by `thread_id` (falling back to the
 * mail's own id when absent). Mails within a thread are sorted oldest
 * first; threads are sorted by their latest mail, newest first.
 * `participants` lists the local parts of every address except mine,
 * in first-appearance order.
 */
export function groupByThread(mails: readonly MailItem[], myAddress: string): ThreadGroup[] {
  const groups = new Map<string, MailItem[]>();
  for (const mail of mails) {
    const key = mail.thread_id || mail.id;
    const bucket = groups.get(key);
    if (bucket) {
      bucket.push(mail);
    } else {
      groups.set(key, [mail]);
    }
  }

  const threads: ThreadGroup[] = [];
  for (const [threadId, bucket] of groups) {
    const sorted = [...bucket].sort(byTimestampAsc);
    const latest = sorted[sorted.length - 1] as MailItem;
    const participants: string[] = [];
    for (const mail of sorted) {
      for (const address of allAddresses(mail)) {
        if (address === myAddress) continue;
        const name = localPart(address);
        if (!participants.includes(name)) participants.push(name);
      }
    }
    const unread = sorted.some((mail) => mail.read === false);
    threads.push({ threadId, mails: sorted, participants, latest, count: sorted.length, unread });
  }

  threads.sort((a, b) => (a.latest.timestamp < b.latest.timestamp ? 1 : -1));
  return threads;
}

/**
 * Merge incoming mails into an existing list, deduplicating by mail id
 * (the server emits one `mail.delivered` event per recipient, so the
 * same mail can arrive multiple times). Returns a new array.
 */
export function mergeMails(existing: readonly MailItem[], incoming: readonly MailItem[]): MailItem[] {
  const seen = new Set(existing.map((mail) => mail.id));
  const merged = [...existing];
  for (const mail of incoming) {
    if (seen.has(mail.id)) continue;
    seen.add(mail.id);
    merged.push(mail);
  }
  return merged;
}
