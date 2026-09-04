import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent, ReactElement } from "react";
import type { ServerEvent, ViewProps } from "../types";
import "../styles/settings.css";

interface EnvForm {
  base_url: string;
  api_key: string;
  model: string;
  temperature: string;
}

const ENV_FIELDS: ReadonlyArray<keyof EnvForm> = [
  "base_url",
  "api_key",
  "model",
  "temperature",
];

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
 * SETTINGS — merges the TUI's config + settings views. Upper half is
 * read-only: server config (GET /config) and member configs
 * (GET /admin/config/agents). Lower half edits the LLM env
 * (GET/PUT /admin/settings/env, merge semantics). The api_key is
 * write-only: the server returns it masked (`****` + last 4) and the
 * form shows that as a placeholder — an empty field on save keeps the
 * current key, a typed value replaces it. SSE `config.updated` reloads
 * the read-only sections (the env form is left untouched).
 */
export function SettingsView({ rest, sse }: ViewProps): ReactElement {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [agentConfigs, setAgentConfigs] = useState<Record<string, unknown>[]>([]);
  const [configError, setConfigError] = useState<string | null>(null);

  const [envPath, setEnvPath] = useState<string | null>(null);
  const [configured, setConfigured] = useState(false);
  const [apiKeyMasked, setApiKeyMasked] = useState("");
  // Admin token (ADR 0013): lives in localStorage, sent as Bearer on every
  // request. Never shown back — the field stays empty after save.
  const [adminTokenInput, setAdminTokenInput] = useState("");
  const [adminTokenConfigured, setAdminTokenConfigured] = useState(rest.hasAdminToken);
  const [adminTokenSaved, setAdminTokenSaved] = useState<string | null>(null);
  const [form, setForm] = useState<EnvForm>({
    base_url: "",
    api_key: "",
    model: "",
    temperature: "",
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [envError, setEnvError] = useState<string | null>(null);

  // Read-only section and env form load independently: a 503 on one
  // (e.g. no config manager) must not break the other.
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

  const loadEnv = useCallback(async () => {
    try {
      const data = await rest.getSettingsEnv();
      setEnvPath(data.path);
      setConfigured(data.configured);
      // api_key comes back masked; the field stays empty until the user
      // types a new key (empty on save = keep the current key).
      setApiKeyMasked(data.values.api_key ?? "");
      setForm({
        base_url: data.values.base_url ?? "",
        api_key: "",
        model: data.values.model ?? "",
        temperature: String(data.values.temperature ?? ""),
      });
      setEnvError(null);
    } catch (err) {
      setEnvError(err instanceof Error ? err.message : String(err));
    }
  }, [rest]);

  useEffect(() => {
    void loadConfig();
    void loadEnv();
  }, [loadConfig, loadEnv]);

  // A member config change anywhere (WebUI, TUI, agent tool) reloads the
  // read-only config sections. The env form is deliberately not reloaded —
  // clobbering an in-progress edit would be worse than a stale display.
  useEffect(() => {
    const onEvent = (ev: ServerEvent): void => {
      if (ev.eventType === "config.updated") void loadConfig();
    };
    sse.on("event", onEvent);
    return () => {
      sse.off("event", onEvent);
    };
  }, [sse, loadConfig]);

  const setField =
    (field: keyof EnvForm) =>
    (e: ChangeEvent<HTMLInputElement>): void => {
      setForm((prev) => ({ ...prev, [field]: e.target.value }));
      setSaved(null);
    };

  const save = async (): Promise<void> => {
    setSaving(true);
    setSaved(null);
    try {
      const temp = Number.parseFloat(form.temperature);
      await rest.updateSettingsEnv({
        base_url: form.base_url.trim() || undefined,
        api_key: form.api_key.trim() || undefined,
        model: form.model.trim() || undefined,
        temperature: Number.isFinite(temp) ? temp : undefined,
      });
      setSaved("已保存 — 重启 dream 生效");
      setEnvError(null);
      void loadEnv();
    } catch (err) {
      setEnvError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const saveAdminToken = (token: string): void => {
    rest.setAdminToken(token);
    setAdminTokenConfigured(rest.hasAdminToken);
    setAdminTokenInput("");
    setAdminTokenSaved(token.trim() ? "已保存 — 立即生效" : "已清除");
    // Panels that 401'd before the token was set get a fresh chance.
    void loadConfig();
    void loadEnv();
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
            void loadEnv();
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
          LLM ENV · GET/PUT /admin/settings/env
        </h3>
        <div className="settings-status-row">
          <span className="tech-label">API KEY</span>
          <span
            className={`tech-label ${configured ? "settings-status--ok" : "settings-status--warn"}`}
          >
            {configured ? "CONFIGURED" : "NOT CONFIGURED — 认知成员首次调用 LLM 会失败"}
          </span>
        </div>
        <div className="settings-form">
          {ENV_FIELDS.map((field) => (
            <div className="settings-field" key={field}>
              <label className="tech-label" htmlFor={`env-${field}`}>
                {field}
              </label>
              <input
                id={`env-${field}`}
                className="input"
                type="text"
                value={form[field]}
                placeholder={
                  field === "api_key"
                    ? apiKeyMasked || "未配置 — 输入新 key 并保存"
                    : undefined
                }
                onChange={setField(field)}
                disabled={saving}
                spellCheck={false}
                autoComplete="off"
              />
            </div>
          ))}
        </div>
        <div className="settings-actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void save()}
            disabled={saving}
          >
            {saving ? "SAVING…" : "SAVE"}
          </button>
          {saved ? <span className="tech-label settings-saved">{saved}</span> : null}
          {envError ? <span className="tech-label settings-error">{envError}</span> : null}
        </div>
        {envPath ? <div className="settings-path">写入: {envPath}</div> : null}
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
