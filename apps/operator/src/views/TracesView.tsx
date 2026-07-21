import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";
import { JsonDetail, ScrollList, useListHeight } from "../components/ScrollList.js";

type TraceRecord = Record<string, unknown>;

function traceId(trace: TraceRecord): string {
  return String(trace["trace_id"] ?? trace["id"] ?? "?");
}

function traceLabel(trace: TraceRecord): string {
  const id = traceId(trace);
  const extras: string[] = [];
  for (const key of ["name", "source", "status", "root_agent"]) {
    const value = trace[key];
    if (typeof value === "string" && value) extras.push(value);
  }
  return extras.length > 0 ? `${id} (${extras.join(" · ")})` : id;
}

export function TracesView({ rest }: { rest: RestClient }) {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [selected, setSelected] = useState(0);
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const height = useListHeight();

  const load = useCallback(async () => {
    try {
      const list = await rest.listTraces(100);
      setTraces(list);
      setError(null);
      setSelected((prev) => Math.min(prev, Math.max(0, list.length - 1)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  useInput((input, key) => {
    if (key.upArrow) {
      setSelected((prev) => Math.max(0, prev - 1));
      setDetail(null);
    } else if (key.downArrow) {
      setSelected((prev) => Math.min(Math.max(0, traces.length - 1), prev + 1));
      setDetail(null);
    } else if (input === "r") {
      void load();
    } else if (key.return) {
      const trace = traces[selected];
      if (trace) {
        rest
          .getTrace(traceId(trace))
          .then(setDetail)
          .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
      }
    }
  });

  const selectedTrace = traces[selected] ?? null;

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Traces</Text>
        <Text dimColor>↑↓ select · enter detail · r refresh</Text>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
      <Box>
        <Box flexDirection="column" width="45%" borderStyle="round" borderColor="gray" paddingX={1}>
          <ScrollList
            items={traces}
            selected={selected}
            height={height}
            renderItem={(item, _index, isSelected) => (
              <Text inverse={isSelected}>{traceLabel(item as TraceRecord)}</Text>
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
          <JsonDetail value={detail ?? selectedTrace} height={height} />
        </Box>
      </Box>
    </Box>
  );
}
