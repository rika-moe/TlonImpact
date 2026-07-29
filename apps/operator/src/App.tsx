import { Box, Text, useApp, useInput, useStdout } from "ink";
import React, { useEffect, useMemo, useState } from "react";
import { RestClient } from "./client/restClient.js";
import { WsClient } from "./client/wsClient.js";
import { Header } from "./components/Header.js";
import { AgentsView } from "./views/AgentsView.js";
import { ConfigView } from "./views/ConfigView.js";
import { EventsView } from "./views/EventsView.js";
import { MailView } from "./views/MailView.js";
import { TracesView } from "./views/TracesView.js";
import { VIEWS, type ConnectionStatus, type ViewName } from "./types.js";

export default function App({
  host,
  wsPort,
  restPort,
  username,
}: {
  host: string;
  wsPort: number;
  restPort: number;
  username: string;
}) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const [view, setView] = useState<ViewName>("mail");
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", attempt: 0 });
  // While a mail form captures text input, global view-switch keys are off.
  const [mailEditing, setMailEditing] = useState(false);

  const ws = useMemo(
    () => new WsClient({ host, port: wsPort, clientType: "tui", username }),
    [host, wsPort, username],
  );
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
    if (key.ctrl && input === "c") {
      exit();
      return;
    }
    if (mailEditing) return; // a mail form owns the keyboard
    if (input === "q") {
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
      <Header
        activeView={view}
        status={status}
        server={`${host}:${wsPort}/${restPort}`}
        username={username}
      />
      <Box flexGrow={1} flexDirection="column" marginTop={1}>
        {view === "mail" ? (
          <MailView ws={ws} rest={rest} username={username} onEditingChange={setMailEditing} />
        ) : null}
        {view === "agents" ? <AgentsView rest={rest} ws={ws} /> : null}
        {view === "events" ? <EventsView ws={ws} /> : null}
        {view === "traces" ? <TracesView rest={rest} /> : null}
        {view === "config" ? <ConfigView rest={rest} /> : null}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>1-5 / tab / ←→ switch view · q quit</Text>
      </Box>
    </Box>
  );
}
