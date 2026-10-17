import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactElement } from "react";
import { IdentityController } from "./lib/identity";
import { RestClient } from "./client/rest";
import { ServerHealthBadge } from "./components/ServerHealthBadge";
import type { HealthState } from "./components/ServerHealthBadge";
import { SseClient } from "./client/sse";
import type { ConnectionStatus, ViewName } from "./types";
import { VIEWS, VIEW_TITLES } from "./types";
import { MailView } from "./views/MailView";
import { AgentsView } from "./views/AgentsView";
import { PrototypesView } from "./views/PrototypesView";
import { CapabilitiesView } from "./views/CapabilitiesView";
import { EventsView } from "./views/EventsView";
import { TracesView } from "./views/TracesView";
import { MetricsView } from "./views/MetricsView";
import { SessionsView } from "./views/SessionsView";
import { LogsView } from "./views/LogsView";
import { SettingsView } from "./views/SettingsView";

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

const VIEW_COMPONENTS: Record<ViewName, (props: {
  rest: RestClient;
  sse: SseClient;
  username: string;
}) => ReactElement> = {
  mail: MailView,
  agents: AgentsView,
  prototypes: PrototypesView,
  capabilities: CapabilitiesView,
  events: EventsView,
  traces: TracesView,
  metrics: MetricsView,
  sessions: SessionsView,
  logs: LogsView,
  settings: SettingsView,
};

function Operator({ rest, username }: { rest: RestClient; username: string }): ReactElement {
  // Same-origin relative paths: production is served by the server
  // itself under /ui/, dev goes through the vite proxy.
  const sse = useMemo(
    () => new SseClient({ clientType: "webui" }),
    [],
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

export default function App(): ReactElement {
  const rest = useMemo(() => new RestClient(), []);
  const identity = useMemo(() => new IdentityController(rest), [rest]);
  const state = useSyncExternalStore(identity.subscribe, identity.getSnapshot);
  const [username, setUsername] = useState("");
  useEffect(() => { void identity.load(); }, [identity]);
  if (state.identity?.username) {
    return <Operator rest={rest} username={state.identity.username} />;
  }
  return (
    <main className="identity-screen">
      <section className="panel identity-panel">
        <h1 className="glow-title">TLON</h1>
        {state.identity === null ? (
          <>
            <p>{state.busy ? "正在读取你的身份…" : "无法读取 Tlon 身份"}</p>
            {state.error && <p role="alert">{state.error}</p>}
            {!state.busy && <button className="btn btn--primary" onClick={() => void identity.load()}>重试</button>}
          </>
        ) : (
          <form onSubmit={(event) => { event.preventDefault(); void identity.initialize(username); }}>
            <h2>你在 Tlon 中叫什么？</h2>
            <p>设置一次，此后打开即可进入。身份保存在本机 Tlon 实例中。</p>
            <label htmlFor="identity-username">用户名</label>
            <input id="identity-username" className="input" value={username}
              onChange={(event) => setUsername(event.target.value)} required maxLength={64}
              pattern={"[A-Za-z0-9][A-Za-z0-9._\\-]*"} autoComplete="off" spellCheck={false}
              disabled={state.busy} />
            <p>地址：{username.trim() || "username"}@tlon.internal</p>
            <p>使用英文字母、数字、点、下划线或连字符，以字母或数字开头。地址设置后不可更改。</p>
            {state.error && <p role="alert">{state.error}</p>}
            <button className="btn btn--primary" type="submit" disabled={state.busy || !username.trim()}>
              {state.busy ? "正在设置…" : "设置身份并进入"}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
