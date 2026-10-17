/**
 * Thread aggregation for the mail view (pure functions, unit-tested).
 *
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
  const left = Date.parse(a.timestamp);
  const right = Date.parse(b.timestamp);
  const difference = (Number.isFinite(left) ? left : 0) - (Number.isFinite(right) ? right : 0);
  return difference || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/** Every distinct participant address of a mail (sender + to + cc). */
function allAddresses(mail: MailItem): string[] {
  return [mail.from_address, ...mail.to, ...mail.cc];
}

/**
 * Group mails into threads keyed by `thread_id` (falling back to the
 * mail's own id when absent). Mails within a thread are sorted oldest
 * first; threads are sorted by their latest mail, newest first.
 * `participants` lists the complete addresses of every participant except mine,
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
        if (!participants.includes(address)) participants.push(address);
      }
    }
    const unreadCount = sorted.filter((mail) => mail.read === false).length;
    const root = sorted.find((mail) => mail.id === threadId);
    const topic = root ?? (sorted[0] as MailItem);
    const title = topic.subject?.trim() || topic.content.replace(/\s+/g, " ").trim().slice(0, 80) || "（无主题）";
    threads.push({
      threadId, mails: sorted, participants, latest, count: sorted.length,
      unread: unreadCount > 0, unreadCount, title, rootLoaded: root !== undefined,
    });
  }

  threads.sort((a, b) => byTimestampAsc(b.latest, a.latest));
  return threads;
}

/** Search all loaded mail bodies and metadata, not only the latest reply. */
export function threadMatchesFilter(thread: ThreadGroup, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || thread.mails.some((mail) =>
    [mail.subject ?? "", mail.content, ...allAddresses(mail)].join(" ").toLowerCase().includes(needle),
  );
}

/** Merge authoritative fields by id; events without read state preserve the known value. */
export function mergeMails(existing: readonly MailItem[], incoming: readonly MailItem[]): MailItem[] {
  const merged = new Map(existing.map((mail) => [mail.id, mail]));
  for (const mail of incoming) {
    const previous = merged.get(mail.id);
    const next = { ...previous, ...mail };
    if (mail.read === undefined && previous?.read !== undefined) next.read = previous.read;
    merged.set(mail.id, next);
  }
  return [...merged.values()];
}
