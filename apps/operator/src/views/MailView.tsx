/**
 * Mail view: the human's mail console (browse / compose / reply).
 *
 * - Left pane lists conversation threads (grouped by `thread_id`,
 *   newest first); the right pane shows the selected thread's mails
 *   in chronological order.
 * - Live `mail.delivered` events are merged in (deduplicated by mail
 *   id — the server emits one event per recipient). Own sends are not
 *   delivered back, so they are appended on the POST /messages ack
 *   (the response carries the real mail id).
 * - Compose (c) walks four fields To/Cc/Subject/Content with enter;
 *   enter on Content submits, esc cancels. Reply (r) prefills To and
 *   Subject from the latest incoming mail of the selected thread and
 *   carries `in_reply_to` / `thread_id` headers.
 */

import { Box, Text, useInput } from "ink";
import React, { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { RestClient } from "../client/restClient.js";
import type { SseClient } from "../client/sseClient.js";
import { ScrollList, useListHeight } from "../components/ScrollList.js";
import { groupByThread, involvesMe, localPart, mergeMails } from "../mail/threads.js";
import type { MailItem, ParticipantInfo, ServerEvent } from "../types.js";

type Mode = "browse" | "compose" | "reply";

const FIELDS = ["to", "cc", "subject", "content"] as const;
type FieldName = (typeof FIELDS)[number];
const FIELD_LABELS: Record<FieldName, string> = {
  to: "To",
  cc: "Cc",
  subject: "Subject",
  content: "Content",
};

type Form = Record<FieldName, string>;

const EMPTY_FORM: Form = { to: "", cc: "", subject: "", content: "" };

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

export function MailView({
  sse,
  rest,
  username,
  onEditingChange,
}: {
  sse: SseClient;
  rest: RestClient;
  username: string;
  /** Reports whether a text form is capturing input (gates global keys). */
  onEditingChange?: (editing: boolean) => void;
}) {
  const myAddress = `${username}@tlon.internal`;
  const [mails, setMails] = useState<MailItem[]>([]);
  const [participants, setParticipants] = useState<ParticipantInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("browse");
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [field, setField] = useState(0);
  const [formError, setFormError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Recipient autocomplete: cycling highlight among candidates (tab).
  const [candidateIndex, setCandidateIndex] = useState(0);
  // Thread filter ("/" enters filter mode; enter applies, esc clears).
  const [filter, setFilter] = useState("");
  const [filtering, setFiltering] = useState(false);
  const height = useListHeight();

  const requestId = useRef(crypto.randomUUID());
  const replyRef = useRef<{ in_reply_to?: string; thread_id?: string }>({});

  // Initial inbox + address book load.
  useEffect(() => {
    let cancelled = false;
    rest
      .getInbox(username)
      .then((inbox) => {
        if (!cancelled) setMails((prev) => mergeMails(prev, inbox));
      })
      .catch((err: unknown) => {
        if (!cancelled) setNotice(`inbox 加载失败: ${String(err)}`);
      });
    rest
      .listParticipants()
      .then((list) => {
        if (!cancelled) setParticipants(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [rest, username]);

  // Live delivery events.
  useEffect(() => {
    const onEvent = (ev: ServerEvent) => {
      if (ev.eventType !== "mail.delivered") return;
      const payload = ev.payload as { mail?: MailItem } | undefined;
      const mail = payload?.mail;
      if (!mail || typeof mail.id !== "string") return;
      if (!involvesMe(mail, myAddress)) return;
      setMails((prev) => mergeMails(prev, [mail]));
    };
    sse.on("event", onEvent);
    return () => {
      sse.off("event", onEvent);
    };
  }, [sse, myAddress]);

  // Gate global app keys while a form is capturing input.
  useEffect(() => {
    onEditingChange?.(mode !== "browse");
    return () => onEditingChange?.(false);
  }, [mode, onEditingChange]);

  const threads = useMemo(() => groupByThread(mails, myAddress), [mails, myAddress]);

  /** Display name of a participant id (fallback: the id itself). */
  const displayName = (id: string): string => {
    return participants.find((p) => p.id === id)?.name ?? id;
  };

  /** Threads matching the active filter (participant name/id, subject,
   * content — case-insensitive). */
  const visibleThreads = useMemo(() => {
    if (!filter) return threads;
    const f = filter.toLowerCase();
    return threads.filter((t) => {
      const who = t.participants.map((id) => displayName(id)).join(" ");
      const haystack = `${who} ${t.latest.subject ?? ""} ${t.latest.content}`.toLowerCase();
      return haystack.includes(f);
    });
  }, [threads, filter, participants]);

  const selectedIndex = Math.max(
    0,
    visibleThreads.findIndex((thread) => thread.threadId === selectedId),
  );
  const selectedThread = visibleThreads[selectedIndex] ?? null;

  const move = (delta: number) => {
    if (visibleThreads.length === 0) return;
    const next = Math.min(
      visibleThreads.length - 1,
      Math.max(0, selectedIndex + delta),
    );
    setSelectedId(visibleThreads[next]?.threadId ?? null);
    setNotice(null);
  };

  const openCompose = () => {
    replyRef.current = {};
    setForm(EMPTY_FORM);
    setField(0);
    setFormError(null);
    setNotice(null);
    setMode("compose");
  };

  const openReply = () => {
    const target = selectedThread
      ? [...selectedThread.mails].reverse().find((mail) => mail.from_address !== myAddress)
      : undefined;
    if (!target) {
      setNotice("该线程没有可回复的邮件");
      return;
    }
    replyRef.current = { in_reply_to: target.id, thread_id: target.thread_id };
    const base = target.subject ?? target.content.slice(0, 20);
    setForm({
      to: target.from_address,
      cc: "",
      subject: base.startsWith("Re:") ? base : `Re: ${base}`,
      content: "",
    });
    setField(FIELDS.indexOf("content"));
    setFormError(null);
    setNotice(null);
    setMode("reply");
  };

  const closeForm = () => {
    requestId.current = crypto.randomUUID();
    setSending(false);
    setForm(EMPTY_FORM);
    setFormError(null);
    setMode("browse");
  };

  const submit = async () => {
    const to = splitList(form.to);
    if (to.length === 0) {
      setFormError("To 不能为空（逗号分隔，支持名字 / id / 地址）");
      return;
    }
    const content = form.content.trim();
    if (!content) {
      setFormError("Content 不能为空");
      return;
    }
    const cc = splitList(form.cc);
    const subject = form.subject.trim() || undefined;
    const reply = { ...replyRef.current };

    // POST /messages gives a synchronous ack: on success the mail is
    // appended with its real id; on failure the form stays open with its
    // fields intact and the server's error lands back in it.
    setSending(true);
    setFormError(null);
    try {
      const receipt = await rest.sendMail(username, { request_id: requestId.current, to, cc, subject, content, ...reply });
      const mailId = receipt.mail_id;
      setNotice(receipt.message);
      const sent: MailItem = {
        id: mailId,
        from_address: myAddress,
        to,
        cc,
        subject: subject ?? null,
        content,
        in_reply_to: reply.in_reply_to ?? null,
        thread_id: reply.thread_id ?? mailId,
        forward_of: null,
        timestamp: new Date().toISOString(),
        read: true,
      };
      setMails((prev) => mergeMails(prev, [sent]));
      closeForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
      setSending(false);
    }
  };

  /** The recipient segment currently being typed (after the last comma). */
  const currentSegment = (value: string): string => {
    const idx = value.lastIndexOf(",");
    return (idx >= 0 ? value.slice(idx + 1) : value).trim();
  };

  /** Filtered recipient candidates for the active to/cc field. */
  const candidates = useMemo(() => {
    if (mode === "browse" || (field !== 0 && field !== 1)) return [];
    const seg = currentSegment(form[FIELDS[field] as FieldName] ?? "");
    return participants
      .filter((p) => {
        const n = p.name ?? "";
        return seg === "" || n.startsWith(seg) || p.id.startsWith(seg);
      })
      .slice(0, 5);
  }, [mode, field, form, participants]);

  /** Fill the current recipient segment with a candidate.
   *
   * Display names are the addressing surface (main addressing, ADR 0006);
   * a name shared by multiple participants falls back to the stable id so
   * the fill stays unambiguous. */
  const fillCandidate = (c: ParticipantInfo) => {
    const active = FIELDS[field] as FieldName;
    const value = form[active] ?? "";
    const idx = value.lastIndexOf(",");
    const prefix = idx >= 0 ? `${value.slice(0, idx + 1)} ` : "";
    const dup =
      participants.filter((p) => p.name && c.name && p.name === c.name).length > 1;
    const token = dup ? c.id : (c.name ?? c.id);
    setForm((prev) => ({ ...prev, [active]: prefix + token }));
    setCandidateIndex(0);
  };

  /** Display label for a candidate: name, or "name (id)" when duplicated. */
  const candidateLabel = (c: ParticipantInfo): string => {
    const dup =
      participants.filter((p) => p.name && c.name && p.name === c.name).length > 1;
    return dup ? `${c.name ?? c.id} (${c.id})` : (c.name ?? c.id);
  };

  useInput((input, key) => {
    if (mode === "browse") {
      if (filtering) {
        if (key.escape) {
          setFiltering(false);
          setFilter("");
        } else if (key.return) {
          setFiltering(false);
        } else if (key.backspace || key.delete) {
          setFilter((v) => v.slice(0, -1));
        } else if (input && input !== "/" && !key.ctrl && !key.meta) {
          setFilter((v) => v + input);
        }
        return;
      }
      if (key.upArrow) move(-1);
      else if (key.downArrow) move(1);
      else if (input === "c") openCompose();
      else if (input === "r") openReply();
      else if (input === "/") {
        setFiltering(true);
        setFilter("");
      }
      return;
    }

    if (sending) return; // awaiting the send ack; ignore keys

    if (key.escape) {
      closeForm();
      return;
    }
    if (key.return) {
      const active = FIELDS[field] as FieldName;
      if (active === "content") {
        void submit();
      } else {
        setField((prev) => Math.min(FIELDS.length - 1, prev + 1));
      }
      return;
    }
    if (key.upArrow) {
      setField((prev) => Math.max(0, prev - 1));
      return;
    }
    if (key.downArrow) {
      setField((prev) => Math.min(FIELDS.length - 1, prev + 1));
      return;
    }
    if (key.backspace || key.delete) {
      const active = FIELDS[field] as FieldName;
      setForm((prev) => ({ ...prev, [active]: prev[active].slice(0, -1) }));
      return;
    }
    if (key.tab) {
      // Autocomplete: tab fills the highlighted candidate and cycles.
      if ((field === 0 || field === 1) && candidates.length > 0) {
        fillCandidate(candidates[candidateIndex % candidates.length]!);
        setCandidateIndex((i) => i + 1);
      }
      return;
    }
    if (key.ctrl || key.meta) return;
    if (input) {
      const text = input.replace(/[\r\n]/g, "");
      if (!text) return;
      const active = FIELDS[field] as FieldName;
      setForm((prev) => ({ ...prev, [active]: prev[active] + text }));
    }
  });

  if (mode !== "browse") {
    const active = FIELDS[field] as FieldName;
    const addressBook = participants
      .map((p) => p.name ?? p.id)
      .filter((name) => name !== username)
      .join(" · ");
    return (
      <Box flexDirection="column">
        <Box gap={2}>
          <Text bold>{mode === "compose" ? "New mail" : "Reply"}</Text>
          <Text dimColor>enter 下一字段 / 提交 · esc 取消</Text>
          {sending ? <Text color="yellow">sending…</Text> : null}
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          {FIELDS.map((name) => (
            <Box key={name}>
              <Text bold={name === active}>{FIELD_LABELS[name].padEnd(8)}: </Text>
              <Text color={name === active ? "cyan" : undefined}>{form[name]}</Text>
              {name === active ? <Text inverse> </Text> : null}
            </Box>
          ))}
          {(active === "to" || active === "cc") && candidates.length > 0 ? (
            <Box>
              <Text dimColor>tab 补全: </Text>
              {candidates.map((c, i) => (
                <Text
                  key={c.id}
                  inverse={i === (candidateIndex % candidates.length)}
                >
                  {candidateLabel(c)}{" "}
                </Text>
              ))}
            </Box>
          ) : null}
          {formError ? <Text color="red">{formError}</Text> : null}
        </Box>
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Mail</Text>
        <Text dimColor>{myAddress}</Text>
        <Text dimColor>
          c 写信 · r 回复 · / 过滤 · ↑↓ 选线程 · {visibleThreads.length} 线程
        </Text>
        {filtering ? <Text color="cyan">过滤: {filter}</Text> : null}
        {filter && !filtering ? <Text color="cyan">过滤: {filter} (esc 清除)</Text> : null}
        {notice ? <Text color="yellow">{notice}</Text> : null}
      </Box>
      <Box>
        <Box flexDirection="column" width="40%" borderStyle="round" borderColor="gray" paddingX={1}>
          <ScrollList
            items={visibleThreads}
            selected={selectedIndex}
            height={height}
            renderItem={(item, _index, isSelected) => {
              const thread = item as (typeof threads)[number];
              const who = thread.participants.map((id) => displayName(id)).join(", ") || "(me)";
              const snippet = thread.latest.subject ?? thread.latest.content.slice(0, 24);
              return (
                <Text inverse={isSelected}>
                  {thread.unread ? <Text bold>● </Text> : null}
                  <Text bold={isSelected || thread.unread}>{who}</Text>
                  <Text dimColor> · {snippet}</Text>
                  {thread.count > 1 ? <Text dimColor> ({thread.count})</Text> : null}
                </Text>
              );
            }}
          />
        </Box>
        <Box
          flexDirection="column"
          flexGrow={1}
          borderStyle="round"
          borderColor="gray"
          paddingX={1}
          marginLeft={1}
        >
          <Conversation thread={selectedThread} height={height} displayName={displayName} />
        </Box>
      </Box>
    </Box>
  );
}

/** The selected thread's mails, oldest first, tail-visible. */
function Conversation({
  thread,
  height,
  displayName,
}: {
  thread: { mails: MailItem[] } | null;
  height: number;
  displayName: (id: string) => string;
}) {
  const rows: ReactNode[] = [];
  if (thread) {
    for (const mail of thread.mails) {
      const to = [...mail.to, ...mail.cc.map((addr) => `(cc) ${addr}`)]
        .map(localPart)
        .map(displayName)
        .join(", ");
      rows.push(
        <Text key={`${mail.id}-h`} wrap="truncate">
          <Text color="magenta">{displayName(localPart(mail.from_address))}</Text>
          <Text dimColor> → {to} · {formatTime(mail.timestamp)}</Text>
        </Text>,
      );
      if (mail.subject) {
        rows.push(
          <Text key={`${mail.id}-s`} wrap="truncate">
            <Text bold>Subject: {mail.subject}</Text>
          </Text>,
        );
      }
      mail.content.split("\n").forEach((line, i) => {
        rows.push(
          <Text key={`${mail.id}-c${String(i)}`} wrap="truncate">
            {line}
          </Text>,
        );
      });
      rows.push(<Text key={`${mail.id}-x`}> </Text>);
    }
  }
  const visible = rows.slice(-height);
  return (
    <Box flexDirection="column" height={height}>
      {visible.length === 0 ? <Text dimColor>(no thread selected)</Text> : visible}
    </Box>
  );
}
