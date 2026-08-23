import { useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import { RestClient } from "./client/rest";
import type { ServerHealth } from "./client/rest";
import { SseClient } from "./client/sse";
import type { ConnectionStatus, ViewName } from "./types";
import { VIEWS, VIEW_TITLES } from "./types";
import { MailView } from "./views/MailView";
import { AgentsView } from "./views/AgentsView";
import { PrototypesView } from "./views/PrototypesView";
import { EventsView } from "./views/EventsView";
import { TracesView } from "./views/TracesView";
import { MetricsView } from "./views/MetricsView";
import { LogsView } from "./views/LogsView";
import { SettingsView } from "./views/SettingsView";

const USERNAME_KEY = "tlon.username";
const DEFAULT_USERNAME = "guest";

/**
 * Username resolution, same rules as the TUI: `?username=` in the URL
 * wins and is persisted to localStorage; otherwise localStorage; default
 * "guest".
 */
function resolveUsername(): string {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("username")?.trim();
  if (fromQuery) {
    window.localStorage.setItem(USERNAME_KEY, fromQuery);
    return fromQuery;
  }
  return window.localStorage.getItem(USERNAME_KEY)?.trim() || DEFAULT_USERNAME;
}

type BadgeKind = "connected" | "reconnecting" | "down";

function badgeKind(status: ConnectionStatus): BadgeKind {
  if (status.state === "connected") return "connected";
  if (status.state === "disconnected") return "down";
  return "reconnecting"; // connecting / reconnecting / authenticating
}

const BADGE_TEXT: Record<BadgeKind, string> = {
  connected: "CONNECTED",
  reconnecting: "RECONNECTING",
  down: "DOWN",
};

/** Header health poll interval: startup + every 30s. */
const HEALTH_POLL_INTERVAL_MS = 30_000;

/**
 * Server health for the topbar. null = first poll still pending (keep
 * only the SSE badge until the first answer lands).
 */
type HealthState = { kind: "ok"; health: ServerHealth } | { kind: "unreachable" } | null;

/**
 * Server version + runtime badge (GET /health). Degrades gracefully:
 * on request failure only an UNREACHABLE badge replaces the version, no
 * error surfaces elsewhere.
 */
function ServerHealthBadge({ state }: { state: HealthState }): ReactElement | null {
  if (state === null) return null;
  if (state.kind === "unreachable") {
    return <span className="badge badge--down">UNREACHABLE</span>;
  }
  const { version, running } = state.health;
  return (
    <>
      <span className="tech-label">SERVER v{version}</span>
      <span className={`badge badge--${running ? "connected" : "reconnecting"}`}>
        {running ? "RUNNING" : "STOPPED"}
      </span>
    </>
  );
}

const VIEW_COMPONENTS: Record<ViewName, (props: {
  rest: RestClient;
  sse: SseClient;
  username: string;
}) => ReactElement> = {
  mail: MailView,
  agents: AgentsView,
  prototypes: PrototypesView,
  events: EventsView,
  traces: TracesView,
  metrics: MetricsView,
  logs: LogsView,
  settings: SettingsView,
};

export default function App(): ReactElement {
  const [username] = useState(resolveUsername);
  // Same-origin relative paths: production is served by the server
  // itself under /ui/, dev goes through the vite proxy.
  const rest = useMemo(() => new RestClient(), []);
  const sse = useMemo(
    () => new SseClient({ clientType: "webui", username }),
    [username],
  );
  const [status, setStatus] = useState<ConnectionStatus>(() => sse.getStatus());
  const [health, setHealth] = useState<HealthState>(null);
  const [view, setView] = useState<ViewName>("mail");

  useEffect(() => {
    const onStatus = (s: ConnectionStatus): void => setStatus(s);
    sse.on("status", onStatus);
    sse.connect();
    return () => {
      sse.off("status", onStatus);
      sse.dispose();
    };
  }, [sse]);

  // Poll GET /health: once at startup, then every 30s. Failures only
  // flip the header badge to UNREACHABLE — no error spam.
  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await rest.health();
        if (!cancelled) setHealth({ kind: "ok", health: data });
      } catch {
        if (!cancelled) setHealth({ kind: "unreachable" });
      }
    };
    void load();
    const timer = setInterval(() => void load(), HEALTH_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [rest]);

  const kind = badgeKind(status);
  const ActiveView = VIEW_COMPONENTS[view];

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1 className="glow-title">TLON://OPERATOR</h1>
        <span className="tech-label">{window.location.host}</span>
        <ServerHealthBadge state={health} />
        <span className="spacer" />
        <span className="tech-label">USER: {username}</span>
        <span className={`badge badge--${kind}`}>{BADGE_TEXT[kind]}</span>
      </header>
      <nav className="sidenav">
        {VIEWS.map((name) => (
          <button
            key={name}
            type="button"
            className={`nav-item${name === view ? " nav-item--active" : ""}`}
            onClick={() => setView(name)}
          >
            {VIEW_TITLES[name]}
          </button>
        ))}
      </nav>
      <main className="main">
        <ActiveView rest={rest} sse={sse} username={username} />
      </main>
    </div>
  );
}
