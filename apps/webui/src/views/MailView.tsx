/**
 * MAIL view — three columns: thread list (unread ●) | conversation cards |
 * compose modal (To/Cc autocomplete, reply pre-fills in_reply_to/thread_id).
 *
 * The toolbar observation picker switches whose mailbox is being viewed:
 * myself (GET /inbox — read flags shown, compose/reply enabled) or any
 * member (GET /members/{member_id}/mail — read-only: no unread markers,
 * no compose/reply). SSE mail.delivered increments are filtered to the
 * observed member's address.
 *
 * Each conversation card shows the mail's metadata: an unread marker for
 * unread mail, a "↩ RE" reference line resolving in_reply_to against the
 * loaded mails (silently dropped when the parent is not in the inbox), and
 * a "⤳ FWD OF" origin line when the mail is a forward.
 *
 * Ported from apps/operator/src/views/MailView.tsx: same REST/SSE semantics
 * (GET /inbox, GET /participants, POST /messages, SSE mail.delivered),
 * rendering layer rewritten from ink to DOM.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { MailSendError } from "../client/rest";
import type { RestClient } from "../client/rest";
import { groupByThread, involvesMe, localPart, mergeMails } from "../lib/threads";
import { memberAddress, selfMemberId } from "../lib/members";
import type {
  MailItem,
  ParticipantInfo,
  ServerEvent,
  ThreadGroup,
  ViewProps,
} from "../types";
import "../styles/mail.css";

function formatTime(timestamp: string): string {
  try {
    return new Date(timestamp).toLocaleTimeString("en-GB", { hour12: false });
  } catch {
    return "??:??:??";
  }
}

/** Split a comma-separated recipient field into trimmed entries. */
function splitList(value: string): string[] {
  return value
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

/** A human participant is online while it has at least one live session. */
function isOnlineHuman(p: ParticipantInfo): boolean {
  return (
    p.type === "human" &&
    typeof p.metadata?.session_count === "number" &&
    p.metadata.session_count > 0
  );
}

/** The recipient segment currently being typed (after the last comma). */
function currentSegment(value: string): string {
  const idx = value.lastIndexOf(",");
  return (idx >= 0 ? value.slice(idx + 1) : value).trim();
}

/** Open compose/reply request handed to the modal. */
interface ComposeRequest {
  kind: "compose" | "reply";
  to: string;
  subject: string;
  inReplyTo?: string;
  threadId?: string;
}

export function MailView({ rest, sse, username }: ViewProps): ReactElement {
  const myAddress = `${username}@tlon.internal`;
  const selfId = selfMemberId(username);
  // Whose mailbox is being observed; defaults to myself.
  const [observedId, setObservedId] = useState<string>(selfId);
  const [mails, setMails] = useState<MailItem[]>([]);
  const [participants, setParticipants] = useState<ParticipantInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [compose, setCompose] = useState<ComposeRequest | null>(null);

  const readOnly = observedId !== selfId;
  const observedAddress = memberAddress(observedId);

  // Address book load (once; it also feeds the observation picker).
  useEffect(() => {
    let cancelled = false;
    rest
      .listParticipants()
      .then((list) => {
        if (!cancelled) setParticipants(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [rest]);

  // Mailbox load for the observed member: my own inbox (with read flags)
  // or the read-only observer endpoint. Switching the observation target
  // clears everything loaded for the previous one.
  useEffect(() => {
    let cancelled = false;
    setMails([]);
    setSelectedId(null);
    setFilter("");
    setNotice(null);
    setCompose(null);
    setLoading(true);
    const load = observedId === selfId ? rest.getInbox(username) : rest.getMemberMail(observedId);
    load
      .then((inbox) => {
        if (cancelled) return;
        setMails(inbox);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setNotice(`MAIL 加载失败: ${err instanceof Error ? err.message : String(err)}`);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rest, username, observedId, selfId]);

  // Live delivery events (deduplicated by mail id — the server emits one
  // event per recipient): keep only mail involving the observed member.
  useEffect(() => {
    const onEvent = (ev: ServerEvent): void => {
      if (ev.eventType !== "mail.delivered") return;
      const payload = ev.payload as { mail?: MailItem } | undefined;
      const mail = payload?.mail;
      if (!mail || typeof mail.id !== "string") return;
      if (!involvesMe(mail, observedAddress)) return;
      setMails((prev) => mergeMails(prev, [mail]));
    };
    sse.on("event", onEvent);
    return () => {
      sse.off("event", onEvent);
    };
  }, [sse, observedAddress]);

  const threads = useMemo(
    () => groupByThread(mails, observedAddress),
    [mails, observedAddress],
  );

  /** Display name of a participant id (fallback: the id itself). */
  const displayName = (id: string): string =>
    participants.find((p) => p.id === id)?.name ?? id;

  /** Threads matching the filter (participant name/id, subject, content). */
  const visibleThreads = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return threads;
    return threads.filter((t) => {
      const who = t.participants
        .map((id) => participants.find((p) => p.id === id)?.name ?? id)
        .join(" ");
      const haystack = `${who} ${t.latest.subject ?? ""} ${t.latest.content}`.toLowerCase();
      return haystack.includes(f);
    });
  }, [threads, filter, participants]);

  const selectedThread: ThreadGroup | null =
    visibleThreads.find((t) => t.threadId === selectedId) ?? visibleThreads[0] ?? null;

  const openReply = (mail: MailItem): void => {
    const base = mail.subject ?? mail.content.slice(0, 20);
    setCompose({
      kind: "reply",
      to: mail.from_address,
      subject: base.startsWith("Re:") ? base : `Re: ${base}`,
      inReplyTo: mail.id,
      threadId: mail.thread_id,
    });
  };

  const onSent = (mail: MailItem): void => {
    // Own sends are not delivered back via SSE, so append on the ack.
    setMails((prev) => mergeMails(prev, [mail]));
    setCompose(null);
  };

  return (
    <div className="mail-view">
      <div className="mail-toolbar">
        <h2 className="tech-label mail-title">MAIL</h2>
        <ObserverPicker
          username={username}
          participants={participants}
          value={observedId}
          onChange={setObservedId}
        />
        <span className="tech-label">{observedAddress}</span>
        <span className="tech-label">{visibleThreads.length} THREADS</span>
        <span className="mail-spacer" />
        {notice ? <span className="mail-notice">{notice}</span> : null}
        {readOnly ? null : (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => setCompose({ kind: "compose", to: "", subject: "" })}
          >
            ✉ COMPOSE
          </button>
        )}
      </div>
      <div className="mail-layout">
        <section className="panel mail-threads-panel">
          <input
            className="input mail-filter"
            placeholder="FILTER // 参与者 / 主题 / 内容"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="mail-thread-list">
            {loading ? (
              <p className="mail-empty">LOADING…</p>
            ) : visibleThreads.length === 0 ? (
              <p className="mail-empty">NO MAIL // 收件箱为空</p>
            ) : (
              visibleThreads.map((thread) => (
                <ThreadItem
                  key={thread.threadId}
                  thread={thread}
                  active={selectedThread?.threadId === thread.threadId}
                  displayName={displayName}
                  showUnread={!readOnly}
                  onSelect={() => {
                    setSelectedId(thread.threadId);
                    setNotice(null);
                  }}
                />
              ))
            )}
          </div>
        </section>
        <section className="panel mail-conversation-panel">
          <Conversation
            thread={selectedThread}
            mails={mails}
            myAddress={observedAddress}
            displayName={displayName}
            readOnly={readOnly}
            onReply={openReply}
          />
        </section>
      </div>
      {compose ? (
        <ComposeModal
          request={compose}
          rest={rest}
          username={username}
          myAddress={myAddress}
          participants={participants}
          onSent={onSent}
          onClose={() => setCompose(null)}
        />
      ) : null}
    </div>
  );
}

/** Observation-target picker: myself first, then every participant. */
function ObserverPicker({
  username,
  participants,
  value,
  onChange,
}: {
  username: string;
  participants: ParticipantInfo[];
  /** Member id currently observed ("human:<username>" or a bare agent id). */
  value: string;
  onChange: (memberId: string) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const selfId = selfMemberId(username);
  // Self is always a candidate, even when the session list lags behind.
  const candidates: ParticipantInfo[] = [
    { id: selfId, name: username, type: "human" },
    ...participants.filter((p) => p.id !== selfId),
  ];
  const current = candidates.find((c) => c.id === value) ?? (candidates[0] as ParticipantInfo);

  const labelOf = (c: ParticipantInfo): string =>
    c.id === selfId ? `${c.name ?? c.id} · ME` : (c.name ?? c.id);
  const badgeOf = (c: ParticipantInfo): ReactElement => (
    <span className={`tech-label mail-suggest-type mail-suggest-type--${c.type ?? "agent"}`}>
      {(c.type ?? "agent").toUpperCase()}
    </span>
  );

  return (
    <div className="mail-observer">
      <button
        type="button"
        className="btn btn--secondary mail-observer-btn"
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setOpen(false)}
      >
        <span className="mail-observer-label">{labelOf(current)}</span>
        {badgeOf(current)}
        <span className="mail-observer-caret">▾</span>
      </button>
      {open ? (
        <div className="mail-suggest mail-observer-list">
          {candidates.map((c) => (
            <div
              key={c.id}
              className={`mail-suggest-item${c.id === value ? " mail-suggest-item--active" : ""}`}
              // onMouseDown fires before the button's blur closes the list.
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(c.id);
                setOpen(false);
              }}
            >
              <span className="mail-suggest-label">{labelOf(c)}</span>
              <span className="mail-suggest-badges">
                {isOnlineHuman(c) ? (
                  <span className="tech-label mail-online">● ONLINE</span>
                ) : null}
                {badgeOf(c)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ThreadItem({
  thread,
  active,
  displayName,
  showUnread,
  onSelect,
}: {
  thread: ThreadGroup;
  active: boolean;
  displayName: (id: string) => string;
  /** False in observer mode (read state is the member's own, not shown). */
  showUnread: boolean;
  onSelect: () => void;
}): ReactElement {
  const who = thread.participants.map(displayName).join(", ") || "(me)";
  const snippet = thread.latest.subject ?? thread.latest.content.slice(0, 24);
  const unread = showUnread && thread.unread;
  return (
    <div
      className={`mail-thread-item${active ? " mail-thread-item--active" : ""}`}
      onClick={onSelect}
    >
      <div className="mail-thread-line">
        {unread ? <span className="unread-dot">●</span> : null}
        <span className={`mail-thread-who${unread ? " mail-thread-who--unread" : ""}`}>
          {who}
        </span>
        <span className="mail-thread-meta">
          {formatTime(thread.latest.timestamp)}
          {thread.count > 1 ? ` (${String(thread.count)})` : ""}
        </span>
      </div>
      <div className="mail-thread-snippet">{snippet}</div>
    </div>
  );
}

/** The selected thread's mails, oldest first, tail-visible. */
function Conversation({
  thread,
  mails,
  myAddress,
  displayName,
  readOnly,
  onReply,
}: {
  thread: ThreadGroup | null;
  /** All loaded mails, used to resolve in_reply_to / forward_of references. */
  mails: MailItem[];
  myAddress: string;
  displayName: (id: string) => string;
  /** True when observing another member: no unread markers, no REPLY. */
  readOnly: boolean;
  onReply: (mail: MailItem) => void;
}): ReactElement {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const threadId = thread?.threadId ?? null;
  const mailCount = thread?.mails.length ?? 0;

  const byId = useMemo(() => {
    const map = new Map<string, MailItem>();
    for (const mail of mails) map.set(mail.id, mail);
    return map;
  }, [mails]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [threadId, mailCount]);

  if (!thread) {
    return <p className="mail-empty">(no thread selected)</p>;
  }
  return (
    <div className="mail-conversation" ref={scrollRef}>
      {thread.mails.map((mail) => {
        const own = mail.from_address === myAddress;
        const to = [...mail.to, ...mail.cc.map((addr) => `(cc) ${addr}`)]
          .map(localPart)
          .map(displayName)
          .join(", ");
        // Reference lines: an unresolved in_reply_to is dropped silently
        // (the parent may live outside the loaded inbox); an unresolved
        // forward_of falls back to the id prefix.
        const parent = mail.in_reply_to ? byId.get(mail.in_reply_to) : undefined;
        const forwardSource = mail.forward_of ? byId.get(mail.forward_of) : undefined;
        // Observer payloads carry no read flag — treat "read only when
        // explicitly false", and suppress the marker entirely when
        // observing another member (their read cursor is not ours).
        const unread = !readOnly && mail.read === false;
        return (
          <article
            key={mail.id}
            className={`mail-card${own ? " mail-card--own" : ""}${unread ? " mail-card--unread" : ""}`}
          >
            <div className="mail-card-head">
              {unread ? (
                <span className="tech-label mail-card-unread">● UNREAD</span>
              ) : null}
              <span className="tech-label mail-card-from">
                {displayName(localPart(mail.from_address))}
              </span>
              <span className="tech-label mail-card-to">→ {to}</span>
              <span className="mail-card-time">{formatTime(mail.timestamp)}</span>
            </div>
            {parent ? (
              <div className="mail-card-ref">
                ↩ RE {displayName(localPart(parent.from_address))} ·{" "}
                {parent.subject ?? parent.content.slice(0, 24)} · {formatTime(parent.timestamp)}
              </div>
            ) : null}
            {mail.forward_of ? (
              <div className="mail-card-ref">
                ⤳ FWD OF{" "}
                {forwardSource
                  ? `${displayName(localPart(forwardSource.from_address))} · ${forwardSource.subject ?? forwardSource.content.slice(0, 24)}`
                  : `#${mail.forward_of.slice(0, 8)}`}
              </div>
            ) : null}
            {mail.subject ? <div className="mail-card-subject">{mail.subject}</div> : null}
            <div className="mail-card-body">{mail.content}</div>
            {!own && !readOnly ? (
              <div className="mail-card-actions">
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => onReply(mail)}
                >
                  REPLY
                </button>
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

/** Compose / reply modal: To/Cc/Subject/Content + recipient autocomplete. */
function ComposeModal({
  request,
  rest,
  username,
  myAddress,
  participants,
  onSent,
  onClose,
}: {
  request: ComposeRequest;
  rest: RestClient;
  username: string;
  myAddress: string;
  participants: ParticipantInfo[];
  onSent: (mail: MailItem) => void;
  onClose: () => void;
}): ReactElement {
  const [to, setTo] = useState(request.to);
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(request.subject);
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const submit = async (): Promise<void> => {
    const toList = splitList(to);
    if (toList.length === 0) {
      setError("TO 不能为空（逗号分隔，支持名字 / id / 地址）");
      return;
    }
    const body = content.trim();
    if (!body) {
      setError("CONTENT 不能为空");
      return;
    }
    const ccList = splitList(cc);
    const subjectTrimmed = subject.trim() || undefined;

    setSending(true);
    setError(null);
    try {
      const mailId = await rest.sendMail(username, {
        to: toList,
        cc: ccList,
        subject: subjectTrimmed,
        content: body,
        in_reply_to: request.inReplyTo,
        thread_id: request.threadId,
      });
      const sent: MailItem = {
        id: mailId,
        from_address: myAddress,
        to: toList,
        cc: ccList,
        subject: subjectTrimmed ?? null,
        content: body,
        in_reply_to: request.inReplyTo ?? null,
        thread_id: request.threadId ?? mailId,
        forward_of: null,
        timestamp: new Date().toISOString(),
        read: true,
      };
      onSent(sent);
    } catch (err) {
      // The form stays open with its fields intact; the server's semantic
      // reason (recipient_unresolved / empty_content / …) lands in the bar.
      if (err instanceof MailSendError) {
        setError(`SEND FAILED // ${err.reason ?? "error"} — ${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      setSending(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="mail-modal-overlay" onClick={onClose}>
      <div
        className="panel mail-modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <h3 className="tech-label mail-modal-title">
          {request.kind === "compose" ? "✉ COMPOSE" : "✉ REPLY"}
        </h3>
        <RecipientInput
          label="TO"
          value={to}
          onChange={setTo}
          participants={participants}
          autoFocus={request.kind === "compose"}
        />
        <RecipientInput label="CC" value={cc} onChange={setCc} participants={participants} />
        <div className="mail-form-row">
          <span className="tech-label">SUBJECT</span>
          <input
            className="input"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
        <div className="mail-form-row">
          <span className="tech-label">CONTENT</span>
          <textarea
            className="input mail-content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            autoFocus={request.kind === "reply"}
          />
        </div>
        {error ? <div className="mail-error-bar">{error}</div> : null}
        <div className="mail-modal-actions">
          <span className="tech-label">CTRL+ENTER SEND</span>
          <span className="mail-spacer" />
          <button
            type="button"
            className="btn btn--secondary"
            onClick={onClose}
            disabled={sending}
          >
            CANCEL
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void submit()}
            disabled={sending}
          >
            {sending ? "SENDING…" : "SEND"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Recipient field with a participant autocomplete dropdown. */
function RecipientInput({
  label,
  value,
  onChange,
  participants,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  participants: ParticipantInfo[];
  autoFocus?: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);

  const segment = currentSegment(value);
  const candidates = participants
    .filter((p) => {
      const name = p.name ?? "";
      return segment === "" || name.startsWith(segment) || p.id.startsWith(segment);
    })
    .slice(0, 5);
  const showDropdown = open && candidates.length > 0;
  const activeIndex = highlight % Math.max(1, candidates.length);

  /** A name shared by multiple participants falls back to the stable id. */
  const isDup = (c: ParticipantInfo): boolean =>
    participants.filter((p) => p.name && c.name && p.name === c.name).length > 1;
  const labelOf = (c: ParticipantInfo): string =>
    isDup(c) ? `${c.name ?? c.id} (${c.id})` : (c.name ?? c.id);

  /** Fill the current recipient segment with a candidate. */
  const fill = (c: ParticipantInfo): void => {
    const idx = value.lastIndexOf(",");
    const prefix = idx >= 0 ? `${value.slice(0, idx + 1)} ` : "";
    onChange(prefix + (isDup(c) ? c.id : (c.name ?? c.id)));
    setHighlight(0);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (!showDropdown) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % candidates.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + candidates.length) % candidates.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      const candidate = candidates[activeIndex];
      if (candidate) fill(candidate);
    } else if (e.key === "Escape") {
      // Close just the dropdown, not the whole modal.
      e.stopPropagation();
      setOpen(false);
    }
  };

  return (
    <div className="mail-form-row">
      <span className="tech-label">{label}</span>
      <input
        className="input"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => {
          onChange(e.target.value);
          setHighlight(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showDropdown ? (
        <div className="mail-suggest">
          {candidates.map((c, i) => (
            <div
              key={c.id}
              className={`mail-suggest-item${i === activeIndex ? " mail-suggest-item--active" : ""}`}
              // onMouseDown fires before the input's blur closes the dropdown.
              onMouseDown={(e) => {
                e.preventDefault();
                fill(c);
              }}
            >
              <span className="mail-suggest-label">{labelOf(c)}</span>
              <span className="mail-suggest-badges">
                {isOnlineHuman(c) ? (
                  <span className="tech-label mail-online">● ONLINE</span>
                ) : null}
                <span
                  className={`tech-label mail-suggest-type mail-suggest-type--${c.type ?? "agent"}`}
                >
                  {(c.type ?? "agent").toUpperCase()}
                </span>
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
