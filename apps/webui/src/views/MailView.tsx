import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { MailSendError } from "../client/rest";
import type { RestClient } from "../client/rest";
import { groupByThread, localPart, threadMatchesFilter } from "../lib/threads";
import { MailboxController } from "../lib/mailbox";
import { memberAddress, selfMemberId } from "../lib/members";
import type {
  MailItem,
  ParticipantInfo,
  ThreadGroup,
  ViewProps,
} from "../types";
import "../styles/mail.css";

function formatTime(timestamp: string): string {
  try {
    return new Date(timestamp).toLocaleString("en-GB", { hour12: false });
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
  const [participants, setParticipants] = useState<ParticipantInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [compose, setCompose] = useState<ComposeRequest | null>(null);

  const readOnly = observedId !== selfId;
  const observedAddress = memberAddress(observedId);
  const mailbox = useMemo(() => new MailboxController(rest, sse, observedId,
    observedAddress, readOnly), [rest, sse, observedId, observedAddress, readOnly]);
  const { mails, loading, loadingMore, syncing, marking, hasMore, notice } = useSyncExternalStore(
    mailbox.subscribe, mailbox.getSnapshot);
  useEffect(() => {
    mailbox.start();
    setSelectedId(null);
    setFilter("");
    setCompose(null);
    return () => mailbox.stop();
  }, [mailbox]);


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

  const threads = useMemo(
    () => groupByThread(mails, observedAddress),
    [mails, observedAddress],
  );

  /** Display name of a participant id (fallback: the id itself). */
  const displayName = (id: string): string =>
    participants.find((p) => p.id === id || p.id === `human:${id}`)?.name ?? id;

  /** Threads matching the filter (participant name/id, subject, content). */
  const visibleThreads = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return threads;
    return threads.filter((thread) => threadMatchesFilter(thread, f) ||
      thread.participants.some((address) => displayName(localPart(address)).toLowerCase().includes(f)));

  }, [threads, filter, participants]);

  useEffect(() => {
    if (visibleThreads.length && !visibleThreads.some((thread) => thread.threadId === selectedId)) {
      setSelectedId(visibleThreads[0]!.threadId);
    }
  }, [visibleThreads, selectedId]);

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

  const activeMailbox = useRef(mailbox);
  activeMailbox.current = mailbox;

  const onSent = (mail: MailItem, message: string): void => {
    mailbox.addLocal(mail, message);
    if (mailbox === activeMailbox.current) setCompose(null);
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
        <button type="button" className="btn btn--secondary" disabled={syncing}
          onClick={() => void mailbox.refresh()}>{syncing ? "SYNCING…" : "REFRESH MAIL"}</button>
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
            placeholder="FILTER // participants / subject / content"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="mail-thread-list">
            {loading ? (
              <p className="mail-empty">LOADING…</p>
            ) : visibleThreads.length === 0 ? (
              <p className="mail-empty">NO MAIL</p>
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
                  }}
                />
              ))
            )}
          </div>
          {hasMore ? <div className="mail-history-notice">
            <span>Earlier mail is available.</span>
            <button type="button" className="btn btn--secondary" disabled={loadingMore}
              onClick={() => void mailbox.loadMore()}>{loadingMore ? "LOADING…" : "LOAD EARLIER MAIL"}</button>
          </div> : null}
        </section>
        <section className="panel mail-conversation-panel">
          <Conversation
            thread={selectedThread}
            mails={mails}
            myAddress={observedAddress}
            displayName={displayName}
            readOnly={readOnly}
            onReply={openReply}
            marking={marking}
            onRead={(read, target) => { void mailbox.setRead(read, target); }}
          />
        </section>
      </div>
      {compose ? (
        <ComposeModal
          request={compose}
          rest={rest}
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
  const who = thread.participants.map((address) => `${displayName(localPart(address))} <${address}>`).join(", ") || "(me)";
  const unread = showUnread && thread.unread;
  return (
    <div
      className={`mail-thread-item${active ? " mail-thread-item--active" : ""}`}
      onClick={onSelect}
    >
      <div className="mail-thread-line">
        {unread ? <span className="unread-dot">●</span> : null}
        <span className={`mail-thread-who${unread ? " mail-thread-who--unread" : ""}`}>
          {thread.title}
        </span>
        <span className="mail-thread-meta">
          {formatTime(thread.latest.timestamp)}
          {thread.count > 1 ? ` (${String(thread.count)})` : ""}
        </span>
      </div>
      <div className="mail-thread-participants">{who}</div>
      <div className="mail-thread-meta">#{thread.threadId.slice(0, 8)} · {thread.count} {thread.count === 1 ? "message" : "messages"}
        {showUnread ? ` · ${thread.unreadCount} unread` : ""}
        {!thread.rootLoaded ? " · root message not loaded" : ""}</div>
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
  marking,
  onRead,
}: {
  thread: ThreadGroup | null;
  /** All loaded mails, used to resolve in_reply_to / forward_of references. */
  mails: MailItem[];
  myAddress: string;
  displayName: (id: string) => string;
  /** True when observing another member: no unread markers, no REPLY. */
  readOnly: boolean;
  onReply: (mail: MailItem) => void;
  marking: boolean;
  onRead: (read: boolean, target: { mail_id?: string; thread_id?: string }) => void;
}): ReactElement {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const threadId = thread?.threadId ?? null;
  const position = useRef({ threadId: null as string | null, firstId: "", height: 0, top: 0, atBottom: true });
  const savePosition = (): void => {
    const el = scrollRef.current;
    if (el) position.current = { threadId, firstId: thread?.mails[0]?.id ?? "", height: el.scrollHeight,
      top: el.scrollTop, atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 32 };
  };

  const byId = useMemo(() => {
    const map = new Map<string, MailItem>();
    for (const mail of mails) map.set(mail.id, mail);
    return map;
  }, [mails]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const previous = position.current;
    if (previous.threadId !== threadId) el.scrollTop = el.scrollHeight;
    else if (previous.firstId && thread?.mails[0]?.id !== previous.firstId) {
      el.scrollTop = previous.top + el.scrollHeight - previous.height;
    } else if (previous.atBottom) el.scrollTop = el.scrollHeight;
    else el.scrollTop = previous.top;
    savePosition();
  }, [threadId, thread?.mails]);

  if (!thread) {
    return <p className="mail-empty">(no thread selected)</p>;
  }
  return (
    <div className="mail-conversation-layout">
      <div className="mail-conversation-head">
        <h3>{thread.title}</h3>
        <span className="tech-label">THREAD {thread.threadId}</span>
        {!thread.rootLoaded ? <p className="mail-history-notice">The root message is not loaded or is outside this mailbox. Showing loaded messages.</p> : null}
        {!readOnly && thread.mails.some((mail) => mail.to.includes(myAddress) || mail.cc.includes(myAddress)) ?
          <div className="mail-thread-actions">
            <button type="button" className="btn btn--secondary" disabled={marking}
              onClick={() => onRead(true, { thread_id: thread.threadId })}>MARK THREAD AS READ</button>
            <button type="button" className="btn btn--secondary" disabled={marking}
              onClick={() => onRead(false, { thread_id: thread.threadId })}>MARK THREAD AS UNREAD</button>
          </div> : null}
      </div>
      <div className="mail-conversation" ref={scrollRef} onScroll={savePosition}>
      {thread.mails.map((mail) => {
        const own = mail.from_address === myAddress;
        const to = [...mail.to, ...mail.cc.map((addr) => `(cc) ${addr}`)].join(", ");
        // Missing reference targets stay visible so partial history is explicit.
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
                {mail.from_address}
              </span>
              <span className="tech-label mail-card-to">→ {to}</span>
              <span className="mail-card-time">{formatTime(mail.timestamp)}</span>
            </div>
            {parent ? (
              <div className="mail-card-ref">
                ↩ RE {displayName(localPart(parent.from_address))} ·{" "}
                {parent.subject ?? parent.content.slice(0, 24)} · {formatTime(parent.timestamp)}
              </div>
            ) : mail.in_reply_to ? <div className="mail-card-ref">↩ RE #{mail.in_reply_to.slice(0, 8)} · parent message not loaded or outside this mailbox</div> : null}
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
            {!readOnly ? (
              <div className="mail-card-actions">
                {mail.to.includes(myAddress) || mail.cc.includes(myAddress) ?
                  <button type="button" className="btn btn--secondary" disabled={marking}
                    onClick={() => onRead(mail.read !== true, { mail_id: mail.id })}>
                    {mail.read === true ? "MARK AS UNREAD" : "MARK AS READ"}
                  </button> : null}
                {!own ?
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => onReply(mail)}
                >
                  REPLY
                </button> : null}
              </div>
            ) : null}
          </article>
        );
      })}
      </div>
    </div>
  );
}

/** Compose / reply modal: To/Cc/Subject/Content + recipient autocomplete. */
function ComposeModal({
  request,
  rest,
  myAddress,
  participants,
  onSent,
  onClose,
}: {
  request: ComposeRequest;
  rest: RestClient;
  myAddress: string;
  participants: ParticipantInfo[];
  onSent: (mail: MailItem, message: string) => void;
  onClose: () => void;
}): ReactElement {
  const [to, setTo] = useState(request.to);
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(request.subject);
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [requestId] = useState(() => crypto.randomUUID());

  const submitting = useRef(false);
  const submit = async (): Promise<void> => {
    if (submitting.current) return;
    const toList = splitList(to);
    if (toList.length === 0) {
      setError("TO is required (comma-separated names, IDs, or addresses).");
      return;
    }
    const body = content.trim();
    if (!body) {
      setError("CONTENT is required.");
      return;
    }
    const ccList = splitList(cc);
    const subjectTrimmed = subject.trim() || undefined;

    submitting.current = true;
    setSending(true);
    setError(null);
    try {
      const receipt = await rest.sendMail({
        request_id: requestId,
        to: toList,
        cc: ccList,
        subject: subjectTrimmed,
        content: body,
        in_reply_to: request.inReplyTo,
        thread_id: request.threadId,
      });
      const mailId = receipt.mail_id;
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
      onSent(sent, receipt.message);
    } catch (err) {
      // The form stays open with its fields intact; the server's semantic
      // reason (recipient_unresolved / empty_content / …) lands in the bar.
      if (err instanceof MailSendError) {
        setError(`${err.reason === "submission_unknown" ? "RESULT UNKNOWN" : "SEND REJECTED"} // ${err.reason ?? "error"} — ${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      submitting.current = false;
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
