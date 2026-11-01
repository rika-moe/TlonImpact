import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { RestError } from "../client/rest";
import type { GiteaProjectsSnapshot, GiteaStatus, RestClient } from "../client/rest";

/** Human grants for upstream review; development takes place in personal forks. */
export function GiteaProjects({ rest }: { rest: RestClient }): ReactElement {
  const [snapshot, setSnapshot] = useState<GiteaProjectsSnapshot | null>(null);
  const [status, setStatus] = useState<GiteaStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("main");
  const [patterns, setPatterns] = useState("develop, release/**");

  const refresh = useCallback(async (write?: () => Promise<unknown>): Promise<void> => {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      if (write) await write();
      const [projects, service] = await Promise.allSettled([rest.giteaProjects(), rest.giteaStatus()]);
      setStatus(service.status === "fulfilled" ? service.value : null);
      setStatusError(service.status === "rejected");
      if (projects.status === "rejected") throw projects.reason;
      setSnapshot(projects.value);
    } catch (err) {
      setSnapshot(null);
      setError(err instanceof RestError && err.status === 503
        ? "Gitea 尚未就绪。服务恢复后请刷新重试。"
        : err instanceof RestError && err.message.startsWith("REST ")
          ? "无法读取或保存项目，请检查服务状态后刷新重试。"
          : err instanceof Error ? err.message : String(err));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }, [rest]);

  useEffect(() => { void refresh(); }, [refresh]);

  const choose = (value: string): void => {
    setSelected(value);
    const project = snapshot?.projects.find((item) => item.name === value);
    setName(project?.name ?? "");
    setBranch(project?.default_branch ?? "main");
    setPatterns(project?.managed ? project.protected_branches.filter((item) => item !== project.default_branch).join(", ") : "develop, release/**");
  };

  return (
    <section className="panel gitea-projects" aria-labelledby="gitea-projects-title">
      <div className="settings-toolbar">
        <h3 id="gitea-projects-title" className="tech-label settings-section-title">GITEA · 项目与评审权限</h3>
        <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => { void refresh(); }}>刷新状态</button>
      </div>
      <p>成员在自己的 fork 中开发，再向上游提交 PR。所有成员可阅读和讨论；人类授予的 reviewer 可合并 PR，拥有上游写入权限，但没有仓库管理权限。</p>
      <p>在 Gitea 项目页面完成 fork、提交和评审。以下保护规则仅用于上游仓库，保护分支必须通过 PR 合并。</p>
      <section aria-label="Gitea 通知状态">
        <h4>讨论通知</h4>
        {statusError ? <p className="settings-error">通知状态读取失败，请刷新重试。</p>
          : status === null ? <p className="settings-dim">正在读取通知状态…</p>
            : !status.webhooks ? <p className="settings-dim">通知桥接尚未配置。</p>
              : <>
                <p>状态：{status.webhooks.state} · {status.webhooks.message}</p>
                <p>待投递：{status.webhooks.pending ?? 0} · 失败待重试：{status.webhooks.failed ?? 0} · 无匹配收件人：{status.webhooks.unrouted ?? 0} · 已接入仓库：{status.webhooks.repositories ?? 0}</p>
                {status.webhooks.last_rejection && <p className="settings-error">最近拒绝：{status.webhooks.last_rejection}</p>}
                {!!status.webhooks.recent_failures?.length && <ul className="settings-error">{status.webhooks.recent_failures.map((failure) => <li key={`${failure.event_key}/${failure.member_id}`}>{failure.member_id} · 尝试 {failure.attempts} 次：{failure.error}</li>)}</ul>}
              </>}
        <small className="settings-dim">通知邮件将成员带到对应 Issue / PR；讨论仍在 Gitea 中进行。点击刷新查看最新投递状态。</small>
      </section>
      {error && <p role="alert" className="settings-error">{error}</p>}
      {busy && <p role="status" className="settings-dim">正在读取或保存项目状态…</p>}
      {snapshot && <>
        <form className="settings-form" onSubmit={(event) => {
          event.preventDefault();
          const request = { name: name.trim(), create: !selected, default_branch: branch.trim(), protected_branches: [...new Set([branch.trim(), ...patterns.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean)])] };
          void refresh(() => rest.configureGiteaProject(request));
        }}>
          <label className="settings-field">项目操作
            <select disabled={busy} value={selected} onChange={(event) => { choose(event.target.value); }}>
              <option value="">创建私有项目</option>
              {snapshot.projects.map((project) => <option key={project.name} value={project.name}>{project.name} · {project.managed ? "更新保护规则" : "纳入托管"}</option>)}
            </select>
          </label>
          <label className="settings-field">项目名称<input required disabled={busy || !!selected} value={name} onChange={(event) => { setName(event.target.value); }} /></label>
          <label className="settings-field">默认分支<input required disabled={busy || !!selected} value={branch} onChange={(event) => { setBranch(event.target.value); }} /></label>
          <label className="settings-field">额外保护分支<input disabled={busy} value={patterns} onChange={(event) => { setPatterns(event.target.value); }} placeholder="develop, release/**" /></label>
          <small className="settings-dim">多个名称或匹配模式以逗号分隔。保存将替换额外保护规则；默认分支始终受到保护。</small>
          <button type="submit" className="btn" disabled={busy}>{selected ? "保存托管规则" : "创建私有项目"}</button>
        </form>
        {snapshot.projects.length === 0 && <p className="settings-dim">尚无项目。</p>}
        <div className="gitea-project-list">
          {snapshot.projects.map((project) => <article className="gitea-project" key={project.name}>
            <h4>{/^https?:\/\//.test(project.html_url) ? <a href={project.html_url} target="_blank" rel="noreferrer">{project.name}</a> : project.name} · {project.managed ? "已托管" : "未托管"}</h4>
            {project.problems.length > 0 && <ul className="settings-error">{project.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
            {project.managed ? <>
              <p>默认分支：{project.default_branch} · 上游保护规则：{project.protected_branches.join(", ")}</p>
              <p>Reviewers：{project.reviewers.join(", ") || "无"}</p>
              {snapshot.members.map((member) => <label className="gitea-reviewer" key={member.id}>
                <input type="checkbox" aria-label={`${project.name} · ${member.name} 合并权限`} disabled={busy || project.problems.length > 0} checked={project.reviewers.includes(member.username)} onChange={(event) => {
                  const enabled = event.target.checked;
                  void refresh(() => rest.setGiteaReviewer(project.name, member.id, enabled));
                }} />
                <span>{member.name} <small>({member.username})</small> · 可合并 PR</span>
              </label>)}
            </> : <p className="settings-dim">在上方选择此项目并保存托管规则后，可授予合并权限。</p>}
          </article>)}
        </div>
      </>}
    </section>
  );
}
