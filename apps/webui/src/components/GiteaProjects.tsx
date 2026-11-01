import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { RestError } from "../client/rest";
import type { GiteaProjectsSnapshot, RestClient } from "../client/rest";

/** Human grants for upstream review; development takes place in personal forks. */
export function GiteaProjects({ rest }: { rest: RestClient }): ReactElement {
  const [snapshot, setSnapshot] = useState<GiteaProjectsSnapshot | null>(null);
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
      setSnapshot(await rest.giteaProjects());
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
        <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => { void refresh(); }}>刷新项目</button>
      </div>
      <p>成员在自己的 fork 中开发，再向上游提交 PR。所有成员可阅读和讨论；人类授予的 reviewer 可合并 PR，拥有上游写入权限，但没有仓库管理权限。</p>
      <p>在 Gitea 项目页面完成 fork、提交和评审。以下保护规则仅用于上游仓库，保护分支必须通过 PR 合并。</p>
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
