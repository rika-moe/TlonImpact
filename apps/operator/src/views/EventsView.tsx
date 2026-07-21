import { Box, Text, useInput } from "ink";
import React, { useEffect, useState } from "react";
import type { WsClient } from "../client/wsClient.js";
import type { ServerEvent } from "../types.js";
import { JsonDetail, ScrollList, useListHeight } from "../components/ScrollList.js";

const MAX_EVENTS = 500;

function formatTime(ev: ServerEvent): string {
  try {
    return new Date(ev.receivedAt).toLocaleTimeString("en-GB", { hour12: false });
  } catch {
    return "??:??:??";
  }
}

export function EventsView({ ws }: { ws: WsClient }) {
  const [events, setEvents] = useState<ServerEvent[]>([]);
  const [selected, setSelected] = useState(0);
  const [paused, setPaused] = useState(false);
  const height = useListHeight();

  useEffect(() => {
    const onEvent = (ev: ServerEvent) => {
      setEvents((prev) =>
        prev.length >= MAX_EVENTS ? [...prev.slice(prev.length - MAX_EVENTS + 1), ev] : [...prev, ev],
      );
    };
    ws.on("event", onEvent);
    return () => {
      ws.off("event", onEvent);
    };
  }, [ws]);

  // Keep the selection following the newest event unless paused.
  useEffect(() => {
    if (!paused) setSelected(Math.max(0, events.length - 1));
  }, [events, paused]);

  useInput((input, key) => {
    if (key.upArrow) {
      setPaused(true);
      setSelected((prev) => Math.max(0, prev - 1));
    } else if (key.downArrow) {
      setSelected((prev) => {
        const next = Math.min(Math.max(0, events.length - 1), prev + 1);
        if (next >= events.length - 1) setPaused(false);
        return next;
      });
    } else if (input === "c") {
      setEvents([]);
      setSelected(0);
    } else if (input === "p") {
      setPaused((prev) => !prev);
    }
  });

  const selectedEvent = events[selected] ?? null;

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Events</Text>
        <Text dimColor>↑↓ scroll (auto-follow at bottom) · c clear · p pause · {events.length} events</Text>
        {paused ? <Text color="yellow">paused</Text> : null}
      </Box>
      <Box>
        <Box flexDirection="column" width="55%" borderStyle="round" borderColor="gray" paddingX={1}>
          <ScrollList
            items={events}
            selected={selected}
            height={height}
            renderItem={(item, _index, isSelected) => {
              const ev = item as ServerEvent;
              return (
                <Text inverse={isSelected}>
                  <Text dimColor>{formatTime(ev)} </Text>
                  <Text color="magenta">{ev.eventType}</Text>
                  {ev.source ? <Text dimColor> {ev.source}</Text> : null}
                  {ev.traceId ? <Text dimColor> trace:{ev.traceId.slice(0, 8)}</Text> : null}
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
          <JsonDetail value={selectedEvent} height={height} />
        </Box>
      </Box>
    </Box>
  );
}
