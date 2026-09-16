import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent, ReactElement } from "react";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/settings.css";

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

/**
 * SETTINGS — merges the TUI's config + settings views. Read-only: server
 * config (GET /config) and member configs (GET /admin/config/agents),
 * plus the admin-token editor (ADR 0013, browser-local). The process-level
 * LLM env editor is gone (ADR 0016): LLM config is member-level now —
 * the model is picked when creating a member, API keys are the
 * provider's own `{PROVIDER}_API_KEY` env vars. SSE `config.updated`
 * reloads the read-only sections.
 */
export function SettingsView({ rest, sse }: ViewProps): ReactElement {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [agentConfigs, setAgentConfigs] = useState<Record<string, unknown>[]>([]);
  const [configError, setConfigError] = useState<string | null>(null);

  // Admin token (ADR 0013): lives in localStorage, sent as Bearer on every
  // request. Never shown back — the field stays empty after save.
  const [adminTokenInput, setAdminTokenInput] = useState("");
  const [adminTokenConfigured, setAdminTokenConfigured] = useState(rest.hasAdminToken);
  const [adminTokenSaved, setAdminTokenSaved] = useState<string | null>(null);

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

  const saveAdminToken = (token: string): void => {
    rest.setAdminToken(token);
    setAdminTokenConfigured(rest.hasAdminToken);
    setAdminTokenInput("");
    setAdminTokenSaved(token.trim() ? "已保存 — 立即生效" : "已清除");
    // Panels that 401'd before the token was set get a fresh chance.
    void loadConfig();
  };

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

      <div className="panel">
        <h3 className="tech-label settings-section-title">
          ADMIN TOKEN · TLON_ADMIN_TOKEN（ADR 0013）
        </h3>
        <div className="settings-status-row">
          <span className="tech-label">TOKEN</span>
          <span
            className={`tech-label ${adminTokenConfigured ? "settings-status--ok" : "settings-status--warn"}`}
          >
            {adminTokenConfigured
              ? "CONFIGURED — 随所有请求以 Bearer 发送"
              : "NOT CONFIGURED — 服务端开启鉴权后 admin 功能不可用"}
          </span>
        </div>
        <div className="settings-form">
          <div className="settings-field">
            <label className="tech-label" htmlFor="admin-token">
              admin token
            </label>
            <input
              id="admin-token"
              className="input"
              type="password"
              value={adminTokenInput}
              placeholder={
                adminTokenConfigured ? "已配置 — 输入新 token 并保存以替换" : "输入服务端配置的 TLON_ADMIN_TOKEN"
              }
              onChange={(e: ChangeEvent<HTMLInputElement>) => {
                setAdminTokenInput(e.target.value);
                setAdminTokenSaved(null);
              }}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => saveAdminToken(adminTokenInput)}
            disabled={!adminTokenInput.trim()}
          >
            SAVE
          </button>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => saveAdminToken("")}
            disabled={!adminTokenConfigured}
          >
            CLEAR
          </button>
          {adminTokenSaved ? (
            <span className="tech-label settings-saved">{adminTokenSaved}</span>
          ) : null}
        </div>
        <div className="settings-path">仅存浏览器 localStorage（tlon.adminToken），不上传</div>
      </div>
    </section>
  );
}
