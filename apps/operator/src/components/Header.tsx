import { Box, Text } from "ink";
import React from "react";
import { VIEW_TITLES, VIEWS, type ConnectionStatus, type ViewName } from "../types.js";

function statusColor(state: ConnectionStatus["state"]): string {
  switch (state) {
    case "connected":
      return "green";
    case "connecting":
    case "authenticating":
    case "reconnecting":
      return "yellow";
    default:
      return "red";
  }
}

function statusLabel(status: ConnectionStatus): string {
  switch (status.state) {
    case "connected":
      return status.sessionId ? `connected (${status.sessionId})` : "connected";
    case "connecting":
      return "connecting…";
    case "authenticating":
      return "authenticating…";
    case "reconnecting":
      return `reconnecting (attempt ${status.attempt})`;
    default:
      return status.lastError ? `disconnected: ${status.lastError}` : "disconnected";
  }
}

export function Header({
  activeView,
  status,
  server,
}: {
  activeView: ViewName;
  status: ConnectionStatus;
  server: string;
}) {
  return (
    <Box justifyContent="space-between">
      <Box gap={1}>
        <Text bold color="cyan">
          Tlon Operator
        </Text>
        {VIEWS.map((view, index) => (
          <Text key={view} bold={view === activeView} color={view === activeView ? "cyan" : "gray"}>
            {index + 1}:{VIEW_TITLES[view]}
          </Text>
        ))}
      </Box>
      <Box gap={1}>
        <Text dimColor>{server}</Text>
        <Text color={statusColor(status.state)}>● {statusLabel(status)}</Text>
      </Box>
    </Box>
  );
}
