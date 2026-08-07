import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";
import { groupMetrics, type MetricsSnapshot } from "../metrics/snapshot.js";

/** Polling interval; no other view polls (they are ws-driven), so the
 * metrics view sets the convention: refresh every 2s. */
const POLL_INTERVAL_MS = 2_000;

/** Read-only view of the server's in-process metrics (GET /metrics). */
export function MetricsView({ rest }: { rest: RestClient }) {
  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSnapshot(await rest.getMetrics());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useInput((input) => {
    if (input === "r") void load();
  });

  const groups = groupMetrics(snapshot ?? {});

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Metrics</Text>
        <Text dimColor>auto-refresh {POLL_INTERVAL_MS / 1000}s · r refresh</Text>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
      <Box flexDirection="column">
        {groups.map((group) => (
          <Box
            key={group.name}
            flexDirection="column"
            borderStyle="round"
            borderColor="gray"
            paddingX={1}
            marginTop={1}
          >
            <Text bold dimColor>
              {group.name}
            </Text>
            {group.rows.length === 0 ? (
              <Text dimColor>no data</Text>
            ) : (
              group.rows.map((row, index) => (
                <Box key={`${row.metric}:${row.attributes}:${index}`} gap={2}>
                  <Text>{row.metric}</Text>
                  <Text dimColor>{row.attributes}</Text>
                  <Text>{row.value}</Text>
                </Box>
              ))
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
}
