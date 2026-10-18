import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";
import type { SseClient } from "../client/sseClient.js";
import type { ServerEvent } from "../types.js";
import { JsonDetail, ScrollList, useListHeight } from "../components/ScrollList.js";

type AgentRecord = Record<string, unknown>;
type ProtoRecord = Record<string, unknown>;

/**
 * Agents admin view (wizard removal, B): read-only run table plus admin
 * actions — create a member from a prototype, edit persona/LLM, remove a
 * member, and approve/reject staged prototypes.
 */
type Mode =
  | { kind: "browse" }
  | { kind: "create-prototype" }
  | { kind: "create-form" }
  | { kind: "edit" }
  | { kind: "delete" }
  | { kind: "staging" };

const CREATE_FIELDS = ["name", "agent_id", "identity"] as const;
const EDIT_FIELDS = ["identity", "model_name"] as const;
const CREATE_LABELS: Record<string, string> = {
  name: "name",
  agent_id: "agent_id(可选)",
  identity: "identity(可选)",
};
const EDIT_LABELS: Record<string, string> = {
  identity: "identity",
  model_name: "model_name",
};

function agentLabel(agent: AgentRecord): string {
  const id = String(agent["id"] ?? agent["name"] ?? "?");
  const name = typeof agent["name"] === "string" && agent["name"] !== id ? ` ${agent["name"]}` : "";
  const state = typeof agent["state"] === "string" ? agent["state"] : "unknown";
  const wakeAt = state === "sleeping" && typeof agent["wake_at"] === "string"
    ? ` until ${new Date(agent["wake_at"]).toLocaleString()}` : "";
  const pending = agent["stop_pending"] ? " · termination pending" : "";
  return `${id}${name} [${state}]${wakeAt}${pending}`;
}

function stateColor(agent: AgentRecord): string {
  const state = String(agent["state"] ?? "").toLowerCase();
  if (state === "active") return "green";
  if (state === "dead") return "red";
  if (state === "sleeping") return "gray";
  return "yellow";
}

function protoLabel(p: ProtoRecord): string {
  const name = String(p["name"] ?? "?");
  const display = String(p["display_name"] ?? "");
  return display && display !== name ? `${name} — ${display}` : name;
}

export function AgentsView({
  rest,
  sse,
  onEditingChange,
}: {
  rest: RestClient;
  sse: SseClient;
  onEditingChange: (editing: boolean) => void;
}) {
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [prototypes, setPrototypes] = useState<ProtoRecord[]>([]);
  const [staging, setStaging] = useState<ProtoRecord[]>([]);
  const [selected, setSelected] = useState(0);
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>({ kind: "browse" });
  const [field, setField] = useState(0);
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const height = useListHeight();

  const load = useCallback(async () => {
    try {
      const list = await rest.listAgents();
      setAgents(list);
      setError(null);
      setSelected((prev) => Math.min(prev, Math.max(0, list.length - 1)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  const loadStaging = useCallback(async () => {
    try {
      setStaging(await rest.listStaging());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
    void loadStaging();
  }, [load, loadStaging]);

  // Real-time refresh: any agent.* event triggers a reload.
  useEffect(() => {
    const onAgents = (list: unknown) => {
      if (Array.isArray(list)) {
        setAgents(list as AgentRecord[]);
        setError(null);
      }
    };
    const onEvent = (ev: ServerEvent) => {
      if (ev.eventType.startsWith("agent")) void load();
    };
    sse.on("agents", onAgents);
    sse.on("event", onEvent);
    return () => {
      sse.off("agents", onAgents);
      sse.off("event", onEvent);
    };
  }, [sse, load]);

  const backToBrowse = useCallback(() => {
    setMode({ kind: "browse" });
    setNotice(null);
    setDetail(null);
    void load();
    void loadStaging();
  }, [load, loadStaging]);

  const submitCreate = async () => {
    const proto = prototypes[selected];
    if (!proto || !form["name"]) return;
    const name = proto["name"];
    if (typeof name !== "string") return;
    setBusy(true);
    try {
      await rest.createAgent({
        prototype: name,
        name: form["name"].trim(),
        agent_id: form["agent_id"]?.trim() || undefined,
        persona: form["identity"]?.trim() ? { identity: form["identity"].trim() } : undefined,
      });
      setNotice(`created ${form["name"].trim()}`);
      backToBrowse();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submitEdit = async () => {
    const agent = agents[selected];
    const id = agent?.["id"];
    if (typeof id !== "string") return;
    setBusy(true);
    try {
      await rest.updateAgent(id, {
        persona: form["identity"]?.trim() ? { identity: form["identity"].trim() } : undefined,
        llm: form["model_name"]?.trim()
          ? { model_name: form["model_name"].trim() }
          : undefined,
      });
      setNotice(`updated ${id}`);
      backToBrowse();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submitDelete = async () => {
    const agent = agents[selected];
    const id = agent?.["id"];
    if (typeof id !== "string") return;
    setBusy(true);
    try {
      await rest.deleteAgent(id);
      setNotice(`removed ${id}`);
      backToBrowse();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const approveStaging = async (accept: boolean) => {
    const proto = staging[selected];
    const name = proto?.["name"];
    if (typeof name !== "string") return;
    setBusy(true);
    try {
      if (accept) {
        await rest.approvePrototype(name);
        setNotice(`approved ${name}`);
      } else {
        await rest.rejectPrototype(name);
        setNotice(`rejected ${name}`);
      }
      setSelected(0);
      await loadStaging();
      setBusy(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  // Report form ownership so App gates its global view-switch keys.
  useEffect(() => {
    onEditingChange(mode.kind !== "browse" && mode.kind !== "staging" && mode.kind !== "delete");
  }, [mode, onEditingChange]);

  useInput((input, key) => {
    if (mode.kind === "browse") {
      if (key.upArrow) {
        setSelected((prev) => Math.max(0, prev - 1));
        setDetail(null);
      } else if (key.downArrow) {
        setSelected((prev) => Math.min(Math.max(0, agents.length - 1), prev + 1));
        setDetail(null);
      } else if (input === "r") {
        void load();
      } else if (input === "c") {
        setSelected(0);
        setMode({ kind: "create-prototype" });
        void rest
          .listPrototypes()
          .then(setPrototypes)
          .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
      } else if (input === "e" && agents[selected]) {
        setField(0);
        setForm({ identity: "", model_name: "" });
        setMode({ kind: "edit" });
      } else if (input === "d" && agents[selected]) {
        setMode({ kind: "delete" });
      } else if (input === "s") {
        setSelected(0);
        setMode({ kind: "staging" });
        void loadStaging();
      } else if (key.return) {
        const agent = agents[selected];
        const id = agent?.["id"];
        if (typeof id === "string") {
          rest
            .getAgent(id)
            .then(setDetail)
            .catch((err: unknown) =>
              setError(err instanceof Error ? err.message : String(err)),
            );
        }
      }
      return;
    }

    if (busy) return;
    if (key.escape) {
      backToBrowse();
      return;
    }

    if (mode.kind === "create-prototype") {
      if (key.upArrow) setSelected((prev) => Math.max(0, prev - 1));
      else if (key.downArrow)
        setSelected((prev) => Math.min(Math.max(0, prototypes.length - 1), prev + 1));
      else if (key.return && prototypes[selected]) {
        setField(0);
        setForm({ name: "", agent_id: "", identity: "" });
        setMode({ kind: "create-form" });
      }
      return;
    }

    if (mode.kind === "staging") {
      if (key.upArrow) setSelected((prev) => Math.max(0, prev - 1));
      else if (key.downArrow)
        setSelected((prev) => Math.min(Math.max(0, staging.length - 1), prev + 1));
      else if (input === "a" && staging[selected]) void approveStaging(true);
      else if (input === "x" && staging[selected]) void approveStaging(false);
      return;
    }

    if (mode.kind === "delete") {
      if (input === "y") void submitDelete();
      else if (input === "n") backToBrowse();
      return;
    }

    // Form modes: create-form / edit.
    const fields = mode.kind === "create-form" ? CREATE_FIELDS : EDIT_FIELDS;
    const labels = mode.kind === "create-form" ? CREATE_LABELS : EDIT_LABELS;
    if (key.return) {
      const active = fields[field] ?? "";
      const last = fields[fields.length - 1] ?? "";
      if (active === last) {
        if (mode.kind === "create-form") void submitCreate();
        else void submitEdit();
      } else {
        setField((prev) => Math.min(fields.length - 1, prev + 1));
      }
      return;
    }
    if (key.upArrow) {
      setField((prev) => Math.max(0, prev - 1));
      return;
    }
    if (key.downArrow) {
      setField((prev) => Math.min(fields.length - 1, prev + 1));
      return;
    }
    if (key.backspace || key.delete) {
      const active = fields[field] ?? "";
      setForm((prev) => ({ ...prev, [active]: (prev[active] ?? "").slice(0, -1) }));
      return;
    }
    if (key.tab || key.ctrl || key.meta) return;
    if (input) {
      const text = input.replace(/[\r\n]/g, "");
      if (!text) return;
      const active = fields[field] ?? "";
      setForm((prev) => ({ ...prev, [active]: (prev[active] ?? "") + text }));
    }
  });

  const selectedAgent = agents[selected] ?? null;
  const selectedProto = (mode.kind === "create-prototype" ? prototypes : staging)[selected] ?? null;

  if (mode.kind === "create-form" || mode.kind === "edit") {
    const fields = mode.kind === "create-form" ? CREATE_FIELDS : EDIT_FIELDS;
    const labels = mode.kind === "create-form" ? CREATE_LABELS : EDIT_LABELS;
    const title = mode.kind === "create-form" ? "Create member from prototype" : `Edit ${selectedAgent?.["id"] ?? ""}`;
    return (
      <Box flexDirection="column">
        <Box gap={2}>
          <Text bold>{title}</Text>
          <Text dimColor>enter 下一字段 / 提交 · esc 取消</Text>
          {busy ? <Text color="yellow">saving…</Text> : null}
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          {fields.map((name) => {
            const active = name === fields[field];
            return (
              <Box key={name}>
                <Text bold={active}>{labels[name]?.padEnd(16) ?? name}: </Text>
                <Text color={active ? "cyan" : undefined}>{form[name] ?? ""}</Text>
                {active ? <Text inverse> </Text> : null}
              </Box>
            );
          })}
        </Box>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
    );
  }

  if (mode.kind === "create-prototype") {
    return (
      <Box flexDirection="column">
        <Box gap={2}>
          <Text bold>Choose a prototype</Text>
          <Text dimColor>↑↓ select · enter next · esc cancel</Text>
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          <ScrollList
            items={prototypes}
            selected={selected}
            height={height}
            renderItem={(item, _i, isSelected) => (
              <Text inverse={isSelected}>{protoLabel(item as ProtoRecord)}</Text>
            )}
          />
        </Box>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
    );
  }

  if (mode.kind === "staging") {
    return (
      <Box flexDirection="column">
        <Box gap={2}>
          <Text bold>Staged prototypes (awaiting approval)</Text>
          <Text dimColor>↑↓ select · a approve · x reject · esc back</Text>
          {busy ? <Text color="yellow">…</Text> : null}
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          {staging.length === 0 ? (
            <Text dimColor>nothing staged</Text>
          ) : (
            <ScrollList
              items={staging}
              selected={selected}
              height={height}
              renderItem={(item, _i, isSelected) => (
                <Text inverse={isSelected} wrap="truncate">
                  {protoLabel(item as ProtoRecord)} —{" "}
                  {String((item as ProtoRecord)["author"] ?? "unknown")}
                </Text>
              )}
            />
          )}
        </Box>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
    );
  }

  if (mode.kind === "delete") {
    const id = selectedAgent?.["id"];
    return (
      <Box flexDirection="column">
        <Text bold color="red">
          Remove member {String(id ?? "?")}? This deletes its records and home
          archive permanently.
        </Text>
        <Text>
          y confirm · n / esc cancel {busy ? "· removing…" : ""}
        </Text>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
    );
  }

  // browse
  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Agents</Text>
        <Text dimColor>
          ↑↓ select · enter detail · c create · e edit · d remove · s approval · r refresh
        </Text>
        {error ? <Text color="red">{error}</Text> : null}
        {notice ? <Text color="green">{notice}</Text> : null}
      </Box>
      <Box>
        <Box flexDirection="column" width="45%" borderStyle="round" borderColor="gray" paddingX={1}>
          <ScrollList
            items={agents}
            selected={selected}
            height={height}
            renderItem={(item, _index, isSelected) => (
              <Text inverse={isSelected} color={stateColor(item as AgentRecord)}>
                {agentLabel(item as AgentRecord)}
              </Text>
            )}
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
          <JsonDetail value={detail ?? selectedAgent} height={height} />
        </Box>
      </Box>
    </Box>
  );
}
