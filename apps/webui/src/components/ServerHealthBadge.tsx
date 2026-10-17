import type { ReactElement } from "react";
import type { ServerHealth } from "../client/rest";

/**
 * Server health for the topbar. null = first poll still pending (keep
 * only the SSE badge until the first answer lands).
 */
export type HealthState = { kind: "ok"; health: ServerHealth } | { kind: "unreachable" } | null;

/**
 * Server version + runtime badge (GET /health). Degrades gracefully:
 * on request failure only an UNREACHABLE badge replaces the version, no
 * error surfaces elsewhere.
 */
export function ServerHealthBadge({ state }: { state: HealthState }): ReactElement | null {
  if (state === null) return null;
  if (state.kind === "unreachable") {
    return <span className="badge badge--down">UNREACHABLE</span>;
  }
  const { version, status } = state.health;
  const running = status === "healthy";
  return (
    <>
      <span className="tech-label">SERVER v{version}</span>
      <span className={`badge badge--${running ? "connected" : "reconnecting"}`}>
        {running ? "RUNNING" : "STOPPED"}
      </span>
    </>
  );
}
