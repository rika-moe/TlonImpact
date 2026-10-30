import { useEffect, useId, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactElement, ReactNode } from "react";
import type { RestClient } from "../client/rest";
import { ModelSetupController } from "../lib/model-setup";

const DEFAULT_MODELS: Record<string, string> = {
  deepseek: "deepseek/deepseek-flash",
  openai: "openai/gpt-4o-mini",
  anthropic: "anthropic/claude-sonnet-4-5",
  gemini: "gemini/gemini-2.5-flash",
};

function useSetup(rest: RestClient): ModelSetupController {
  const controller = useMemo(() => new ModelSetupController(rest), [rest]);
  useEffect(() => { void controller.load(); }, [controller]);
  return controller;
}

export function ModelSetupGate({ rest, children }: { rest: RestClient; children: ReactNode }): ReactElement {
  const controller = useSetup(rest);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  if (state.status && (!state.status.enabled || !state.status.required)) return <>{children}</>;
  return <main className="identity-screen"><section className="panel identity-panel">
    <h1>连接模型服务</h1>
    <ModelSetupForm rest={rest} controller={controller} />
  </section></main>;
}

export function ModelSettings({ rest }: { rest: RestClient }): ReactElement | null {
  const controller = useSetup(rest);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  if (state.status && !state.status.enabled) return null;
  return <section className="panel"><h3>模型与 API Key</h3><ModelSetupForm rest={rest} controller={controller} /></section>;
}

function ModelSetupForm({ rest, controller }: { rest: RestClient; controller: ModelSetupController }): ReactElement {
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState(DEFAULT_MODELS["deepseek"]);
  const [key, setKey] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const listId = useId();
  useEffect(() => { void rest.listLlmModels().then((items) => setModels(items.map((item) => item.model))).catch(() => {}); }, [rest]);
  if (!state.status) return <>
    <p>{state.busy ? "正在读取配置…" : "无法读取配置。"}</p>
    {state.error && <p role="alert">{state.error}</p>}
    <button className="btn" disabled={state.busy} onClick={() => void controller.load()}>重试</button>
  </>;
  const initial = state.status.initial_allowed && Boolean(state.status.initial_member_ids?.length);
  return <form onSubmit={(event) => {
    event.preventDefault();
    void controller.save({ provider, model, api_key: key, scope: initial ? "initial" : "selected", agent_ids: initial ? [] : selected })
      .then((saved) => { if (saved) setKey(""); });
  }}>
    <p>连接验证会向服务商发送一条简短请求，可能产生少量费用。</p>
    <label>模型服务商<select className="input" value={provider} disabled={state.busy} onChange={(event) => {
      setProvider(event.target.value); setModel(DEFAULT_MODELS[event.target.value] ?? `${event.target.value}/`);
    }}>{state.status.providers?.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
    <label>模型<input className="input" list={listId} value={model} onChange={(event) => setModel(event.target.value)} required disabled={state.busy} /></label>
    <datalist id={listId}>{models.filter((name) => name.startsWith(`${provider}/`)).map((name) => <option key={name} value={name} />)}</datalist>
    <label>API Key<input className="input" type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" required maxLength={4096} disabled={state.busy} /></label>
    <p>凭据保存在当前实例数据目录的 .env 文件中，未经加密。复制数据目录也会复制凭据，请仅与可信的人共享。</p>
    {initial ? <p>将所选模型应用到 {state.status.initial_member_ids?.length} 位初始成员。</p> : <fieldset disabled={state.busy}>
      <legend>应用模型的成员（不选择时仅更新服务商 API Key）</legend>
      {state.status.members?.map((member) => <label key={member.id} style={{ display: "block" }}>
        <input type="checkbox" checked={selected.includes(member.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, member.id] : selected.filter((id) => id !== member.id))} />
        {member.name} · {member.model ?? "未配置模型"}
      </label>)}
    </fieldset>}
    <p>API Key 保存后立即用于新请求。{state.status.initial_allowed ? "首次配置会等待所选成员完成旧任务并自动应用模型，无需重启。" : "已运行成员的模型在下次启动时生效；重启 Tlon 即可应用。"}</p>
    {state.error && <p role="alert">{state.error}</p>}
    {state.saved && <p role="status">验证通过，配置已保存。{state.status.pending_members?.length ? `${state.status.pending_members.length} 位已运行成员将在下次启动时使用新模型。` : "所选成员已就绪。"}</p>}
    <button className="btn btn--primary" type="submit" disabled={state.busy || !key || !model || (state.status.required && state.status.initial_allowed && !initial && !selected.length)}>{state.busy ? "正在验证并应用配置…" : "验证连接并保存"}</button>
  </form>;
}
