import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/settings.css";
import { ModelSettings } from "../components/ModelSetup";

/** Flatten nested config objects into dotted key/value display rows. */
function flatten(obj: Record<string, unknown>, prefix = ""): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  for (const key of Object.keys(obj).sort()) {
    const value = obj[key];
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      rows.push(...flatten(value as Record<string, unknown>, path));
    } else {
      rows.push([path, String(value)]);
    }
  }
  return rows;
}

function formatAgentConfig(agent: Record<string, unknown>): string {
  const parts = [agent["id"], agent["name"], agent["prototype"]]
    .filter((v): v is string => typeof v === "string" && v.length > 0);
  return parts.join(" · ");
}

/** Server/member summaries plus the explicitly enabled portable model form.
 * Model choices remain member-level; only provider credentials are shared.
 * SSE config.updated reloads the read-only summaries.
 */
export function SettingsView({ rest, sse }: ViewProps): ReactElement {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [agentConfigs, setAgentConfigs] = useState<Record<string, unknown>[]>([]);
  const [configError, setConfigError] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    try {
      const [cfg, agents] = await Promise.all([rest.config(), rest.listAgentConfigs()]);
      setConfig(cfg);
      setAgentConfigs(agents);
      setConfigError(null);
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  // A member config change anywhere (WebUI, TUI, agent tool) reloads the
  // read-only config sections.
  useEffect(() => {
    const onEvent = (ev: ServerEvent): void => {
      if (ev.eventType === "config.updated") void loadConfig();
    };
    sse.on("event", onEvent);
    return () => {
      sse.off("event", onEvent);
    };
  }, [sse, loadConfig]);

  return (
    <section className="settings-view">
      <div className="settings-toolbar">
        <h2 className="tech-label settings-title">SETTINGS</h2>
        <span className="settings-spacer" />
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => {
            void loadConfig();
          }}
        >
          REFRESH
        </button>
      </div>

      <ModelSettings rest={rest} />
      <div className="settings-grid">
        <div className="panel">
          <h3 className="tech-label settings-section-title">SERVER CONFIG · GET /config</h3>
          {configError ? (
            <div className="tech-label settings-error">{configError}</div>
          ) : config === null ? (
            <div className="tech-label settings-dim">LOADING…</div>
          ) : (
            flatten(config).map(([key, value]) => (
              <div className="kv-row" key={key}>
                <span className="tech-label">{key}</span>
                <span className="kv-value">{value}</span>
              </div>
            ))
          )}
        </div>
        <div className="panel">
          <h3 className="tech-label settings-section-title">
            MEMBER CONFIGS · GET /admin/config/agents
          </h3>
          {agentConfigs.length === 0 ? (
            <div className="tech-label settings-dim">NO MEMBERS</div>
          ) : (
            agentConfigs.map((agent, index) => (
              <div className="kv-row" key={String(agent["id"] ?? index)}>
                <span className="kv-value">{formatAgentConfig(agent)}</span>
              </div>
            ))
          )}
        </div>
      </div>

    </section>
  );
}
