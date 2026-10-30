# Tlon 开发指南


## 项目规范

1. 不保留向后兼容。过时的直接删，别加兼容层、别写migration、别留fallback。
2. 选能满足当前需求的最简单实现。不要预防性抽象，不要多此一举的配置层。
3. 系统分层长。先跑通一个最小的端到端版本，再往上加东西。绝不为了未完成的复杂度拆掉能跑的东西。
4. 组件保持模块化，关注点分离。
5. 优先用成熟的、有人维护的库。没有明确理由别自己重写。
6. 先翻项目里已有的依赖能做什么，再考虑加新包或自己写。别上来就假设库里没有。
7. 架构决策往长了做。不接受"先这样以后再换"的临时方案。
8. 先看成熟产品怎么解决同一个问题，用已验证的模式，别从零发明。

## 快速开始

```bash
# 安装依赖
uv sync

# 配置环境（Tlon 读 ~/.tlon/.env，.env.example 是模板）
cp .env.example ~/.tlon/.env
# 编辑 ~/.tlon/.env，按所用 provider 填 API key（{PROVIDER}_API_KEY，如 DEEPSEEK_API_KEY）
# 模型是成员级配置（ADR 0016）：创建成员时在 WebUI 选择，落 agent.yaml 的 llm.model_name

# 运行服务端与 WebUI
uv run tlon dream     # 服务端，REST + SSE，默认 127.0.0.1:8080；启动 banner 显示 WebUI 地址
# 浏览器 WebUI 由 dream 托管于 /ui/（需先 cd apps/webui && npm install && npm run build）

# 单元测试
uv run pytest
```

## 包结构（ADR 0019）

| 包 | 职责 |
| --- | --- |
| `tlosure` | DSL 语言核心（EDN 解析、燃料计费求值、静态校验、通用词汇），不依赖任何 tlon 包 |
| `tlon` | runtime：EventBus、注册表、成员装配、邮件组织、记忆、工具、沙箱；经 `tlon.api` 暴露 domain facade 与 `build_runtime()` |
| `tlon_server` | serving 进程：REST/SSE、session/notification、WebUI 托管；只经 `tlon.api` 的 facade 触达 runtime |
| `tlon_launcher` | `tlon` CLI 入口（dream 子命令），装配上面各包 |

依赖方向：`tlosure ← tlon ← tlon_server ← tlon_launcher`（单向，import-linter 固话）。文件系统路径无全局单例：`TlonPaths` 在进程入口构造一次，显式注入贯穿各层。

-- 
## 开发规范
- **禁止直接推送 main 分支** — 所有修改必须通过分支 + PR
- 合理划分模块，高内聚，低耦合
- 排查bug要深挖根因，纵览全局，防止后续出现相同bug
- **必须使用 type hint 类型注解**
- 必须使用lint工具: `uv run ruff`

---

## 协作工作流（人与 AI 结对）

### 流程
- 一切改动走分支 + PR；PR 合并（squash）后先 `checkout main && pull` 再拉新分支
- git 操作（建分支 / commit / push / 创建 PR）等用户明确指令，不擅自执行
- 方向性工作先讨论对齐、用户拍板后才写代码；拍板的决策先写进 docs 再实施
- 大任务拆阶段，每阶段独立提交或独立 PR；行为不变先行，能力扩张随后

### 执行
- 大活派 subagent：brief 给精确的文件:行号、已核实的现状事实、已定稿决策、测试要求、验证命令；超时/中断用 resume 续跑；完成后必须自己复核（质量门 + 审 diff + 真实冒烟），不盲信汇报
- 验证质量门：一条命令 `scripts/check_all.sh`（分组 `--static` 静态检查+TS 门禁 / `--unit` / `--integration`，可组合，无参全量；逐项跑完并汇总，任一失败退出码 1），全绿才算完。检查项只在脚本内定义（单一事实源），CI 三个 workflow（static / unit-test / integration-test）与 pre-commit 均调同一脚本分组；TS 门禁要求 apps 下已 `npm install`，缺失时本地跳过、CI 必跑
- 真实冒烟：以隔离 `TLON_HOME` 起 dream + HTTP 发邮件 + 查该实例持久化收件箱验证；完事清理临时数据和进程、释放 8080 端口，不触碰真实 `~/.tlon`

### EPIC 施工流程（AI 自服务闭环）

EPIC issue（任务清单型）的 standardized 施工法，已在 // 验证。默认仍是「等人类合并」；用户明确说「自己合并」后才进入自服务闭环。

1. **拆分**：一个任务项一个 PR，严格串行。施工顺序按文件冲突邻接排（改同一区域的项相邻施工），有依赖的按依赖序。
2. **每个 PR 的循环**：
   - `checkout main && pull`，拉新分支（`fix|feat|perf(<域>): 描述`）
   - 派 coder subagent 实现：brief 给文件:行号、已核实事实、设计约束（边界/不做什么）、测试要求、质量门命令、**禁止 git 操作**
   - 主会话复核：审 diff（重点抽查 subagent 声明的关键事实，如接口签名、调用方、共享可变性），亲自重跑质量门——不盲信汇报
   - commit（conventional 格式 + 中文描述 + `Refs #issue`）→ push → `gh pr create`（body：修复内容/实现/设计决策/测试/验证结果/偏离说明）→ `gh pr checks <N> --watch` 等 CI → 绿后 `gh pr merge <N> --squash` → 删本地分支 → 下一个
3. **收尾**：全部合并后回 EPIC issue 勾选任务清单并标注 PR 号。
4. **事故处理**（都是实战踩过的）：
   - subagent 超时：先查工作区落盘情况——改动完整则主会话接手审查验证后自行提交；不完整或无产出则 resume 续跑
   - `gh` 网络 EOF：直接重试；重试前先确认真实状态（PR 是否已建、merge 是否已发生），防重复操作
   - 施工中发现既有文档欠账（前序 PR 漏同步的描述）：顺带修，在 PR 描述中注明
   - 网络抖动后 `git pull` 失败导致分支基点错误的，删了重建分支再继续

### 质量准则
- 既有测试无需保障通过率， 没通过的单元测试可以直接删除明
- 文档与代码同步：设计文档随代码更新；历史达成情况保留，被废止的决策用删除线或新决策覆盖
- 诚实优先：review 给证据说话；发现用户指令有问题时摆数据提出，由用户定夺

### 沟通
- 汇报带实质：改动清单、测试数、质量门结果、与计划的偏差及原因
- 未完成/未验证的不说成完成；过程事故（subagent 超时、替换误伤等）如实记录

---

*最后更新：2026-09-05*
