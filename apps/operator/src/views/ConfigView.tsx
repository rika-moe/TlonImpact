import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";
import { JsonDetail, useListHeight } from "../components/ScrollList.js";

/** Read-only view of the server configuration and agent configurations. */
export function ConfigView({ rest }: { rest: RestClient }) {
  const [config, setConfig] = useState<unknown>(null);
  const [agentConfigs, setAgentConfigs] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const height = useListHeight();
  const sectionHeight = Math.max(2, Math.floor((height - 1) / 2));

  const load = useCallback(async () => {
    try {
      const [cfg, agents] = await Promise.all([rest.config(), rest.listAgentConfigs()]);
      setConfig(cfg);
      setAgentConfigs(agents);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  useInput((input) => {
    if (input === "r") void load();
  });

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Config (read-only)</Text>
        <Text dimColor>r refresh</Text>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
      <Box flexDirection="column">
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          <Text bold dimColor>
            GET /config
          </Text>
          <JsonDetail value={config} height={sectionHeight} />
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1} marginTop={1}>
          <Text bold dimColor>
            GET /admin/config/agents
          </Text>
          <JsonDetail value={agentConfigs} height={sectionHeight} />
        </Box>
      </Box>
    </Box>
  );
}
