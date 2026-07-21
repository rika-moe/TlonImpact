import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";
import type { WsClient } from "../client/wsClient.js";
import type { ServerEvent } from "../types.js";
import { JsonDetail, ScrollList, useListHeight } from "../components/ScrollList.js";

type AgentRecord = Record<string, unknown>;

function agentLabel(agent: AgentRecord): string {
  const id = String(agent["id"] ?? agent["name"] ?? "?");
  const name = typeof agent["name"] === "string" && agent["name"] !== id ? ` ${agent["name"]}` : "";
  const state = typeof agent["state"] === "string" ? agent["state"] : "unknown";
  return `${id}${name} [${state}]`;
}

function stateColor(agent: AgentRecord): string {
  const state = String(agent["state"] ?? "").toLowerCase();
  if (["running", "active", "idle", "online"].includes(state)) return "green";
  if (["stopped", "offline", "error", "failed"].includes(state)) return "red";
  return "yellow";
}

export function AgentsView({ rest, ws }: { rest: RestClient; ws: WsClient }) {
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [selected, setSelected] = useState(0);
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
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

  useEffect(() => {
    void load();
  }, [load]);

  // Real-time refresh: initial agent snapshot + any agent.* event triggers a reload.
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
    ws.on("agents", onAgents);
    ws.on("event", onEvent);
    return () => {
      ws.off("agents", onAgents);
      ws.off("event", onEvent);
    };
  }, [ws, load]);

  useInput((input, key) => {
    if (key.upArrow) {
      setSelected((prev) => Math.max(0, prev - 1));
      setDetail(null);
    } else if (key.downArrow) {
      setSelected((prev) => Math.min(Math.max(0, agents.length - 1), prev + 1));
      setDetail(null);
    } else if (input === "r") {
      void load();
    } else if (key.return) {
      const agent = agents[selected];
      const id = agent?.["id"];
      if (typeof id === "string") {
        rest
          .getAgent(id)
          .then(setDetail)
          .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
      }
    }
  });

  const selectedAgent = agents[selected] ?? null;

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Agents</Text>
        <Text dimColor>↑↓ select · enter detail · r refresh</Text>
        {error ? <Text color="red">{error}</Text> : null}
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
