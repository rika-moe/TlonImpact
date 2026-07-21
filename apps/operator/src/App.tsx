import { Box, Text, useApp, useInput, useStdout } from "ink";
import React, { useEffect, useMemo, useState } from "react";
import { RestClient } from "./client/restClient.js";
import { WsClient } from "./client/wsClient.js";
import { Header } from "./components/Header.js";
import { AgentsView } from "./views/AgentsView.js";
import { ConfigView } from "./views/ConfigView.js";
import { EventsView } from "./views/EventsView.js";
import { TracesView } from "./views/TracesView.js";
import { VIEWS, type ConnectionStatus, type ViewName } from "./types.js";

export default function App({
  host,
  wsPort,
  restPort,
}: {
  host: string;
  wsPort: number;
  restPort: number;
}) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const [view, setView] = useState<ViewName>("agents");
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", attempt: 0 });

  const ws = useMemo(() => new WsClient({ host, port: wsPort, clientType: "tui" }), [host, wsPort]);
  const rest = useMemo(() => new RestClient(host, restPort), [host, restPort]);

  useEffect(() => {
    const onStatus = (next: ConnectionStatus) => setStatus(next);
    const onAuthenticated = () => {
      // Operator is a monitoring console: switch to "event" mode so the
      // server pushes all event types (see notification_service.py).
      ws.setMode("event");
    };
    ws.on("status", onStatus);
    ws.on("authenticated", onAuthenticated);
    ws.connect();
    return () => {
      ws.off("status", onStatus);
      ws.off("authenticated", onAuthenticated);
      ws.dispose();
    };
  }, [ws]);

  useInput((input, key) => {
    if (input === "q" || (key.ctrl && input === "c")) {
      exit();
      return;
    }
    const index = VIEWS.indexOf(view);
    const digit = Number.parseInt(input, 10);
    if (digit >= 1 && digit <= VIEWS.length) {
      setView(VIEWS[digit - 1] as ViewName);
    } else if (key.tab || key.rightArrow) {
      setView(VIEWS[(index + 1) % VIEWS.length] as ViewName);
    } else if (key.leftArrow) {
      setView(VIEWS[(index - 1 + VIEWS.length) % VIEWS.length] as ViewName);
    }
  });

  const rows = stdout?.rows ?? 24;

  return (
    <Box flexDirection="column" height={rows}>
      <Header activeView={view} status={status} server={`${host}:${wsPort}/${restPort}`} />
      <Box flexGrow={1} flexDirection="column" marginTop={1}>
        {view === "agents" ? <AgentsView rest={rest} ws={ws} /> : null}
        {view === "events" ? <EventsView ws={ws} /> : null}
        {view === "traces" ? <TracesView rest={rest} /> : null}
        {view === "config" ? <ConfigView rest={rest} /> : null}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>1-4 / tab / ←→ switch view · ↑↓ select · enter detail · r refresh · q quit</Text>
      </Box>
    </Box>
  );
}
