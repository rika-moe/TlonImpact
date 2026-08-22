# 外部调研：DeepSeek Harness（dsh）对 Tlon 的可借鉴点

> 调研对象：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（2026-08-21，TypeScript pnpm monorepo，口号 "Everything is a Plugin"，基于 Cordis——《A Programming Paradigm for Spatiotemporal Composability》）。本文是方向讨论的输入材料，不含已拍板决策；其中任何条目若要实施，先讨论对齐、再（如涉及架构）补 ADR。

## 定位差异（形似神不同）

- **dsh 是单 agent coding harness**（Claude Code 同类）：产品是 `dsh` CLI，内置 `web`（HTTP server + 浏览器 UI）与 `headless` 两个 profile；多 agent 能力（subagent / agent teams / workflow）是挂在单 agent loop 旁的可选 seam。
- **Tlon 是多 agent 社会**：邮件是唯一通信方式，行为即数据（EDN spec），自进化是目标。
- 重叠层是「agent 运行时工程」：循环、工具调度、上下文管理、可观测性、持久化、客户端协议。dsh 在这一层非常成熟，且多条机制与 Tlon 已有哲学同构。

## 强烈契合，建议优先借鉴

### 1. Session 事件溯源 +「模型可见 ⇒ 已入日志」不变式 ✅ 已实施（2026-08-22）

dsh 做法（`docs/subsystems/session.md`）：session 是 append-only `SessionEvent` 日志，是**唯一真相**；模型历史由 `deriveMessages()` 从日志推导，从不单独存储；replay = 重推导；崩溃恢复不截断，而是给孤儿 turn 补一个合成的 `turn/end{reason:'interrupted'}`；「模型可见的内容必定已入日志」有 runtime invariant 断言把关。token 级的 `assistant/chunk` 也落日志，回放保真。

Tlon 现状：邮件是磁盘事实（mailbox 即队列，先持久化后处理），但认知循环的 `AgentSession` 是内存态，进程重启 session 即蒸发；traces 落盘是观测而非真相源。

借鉴形态：`AgentSession` 的 think/plan/act/critique/revise 产出与工具结果落 JSONL 事件日志，重启后重放恢复——与「行为即数据」哲学同构：**认知过程也是数据**。

**实施记录**：[ADR 0007](../decisions/0007-session-event-sourcing.md) + [agents/session_journal.py](../../src/tlon/agents/session_journal.py)（append/resume/derive_context，孤儿 dispatch 自愈为合成 interrupted 结果）；`LoopDriver` 所有状态变更先落日志再改内存（`session/start` 于首个 stage 前，`tool/result` 在后台 task 内即写），`SessionScheduler` 按日志恢复（completed 只标读、孤儿工具走 `resume_collect`、超时/异常补 `session/end`）；装配经 `CognitiveCapabilities.tlon_paths` 注入。token 级 chunk 未落日志（Tlon 的 LLM 调用非流式，无 chunk 可记）。测试见 [src/tests/agents/test_session_journal.py](../../src/tests/agents/test_session_journal.py)（roundtrip / 坏行容忍 / 崩溃恢复端到端 / 调度准入门）。

### 2. 超长工具输出 spill 落盘 + locator

dsh 做法（`docs/subsystems/spill.md`）：`dsh-spill` 把超长工具输出落盘，给模型返回 locator + 检索提示，在 `tools/post-execute` 策略点触发。

Tlon 现状：shell/filesystem 工具输出直接进上下文，是上下文爆炸的常见来源。

借鉴形态：EffectExecutor 执行工具后按大小阈值落盘到成员 workspace，模型只看到 locator。机制简单、收益直接。

### 3. 上下文压力管理成 seam

dsh 做法（`docs/subsystems/compaction.md`）：`ctx.tokenMeter` 单例估算；压力触发点在 `agent/pre-step`；先做可选 tool-result pruning 再选摘要范围；真溢出恢复走 `agent/request-error`（surface 替换、代际前进才开新 retry turn）；摘要经带 `surfaceOp` 的 `user/message` 落日志（满足第 1 条的不变式）。

Tlon 现状：有记忆摘要器（`memory/summarizer.py`），但认知循环内的上下文压力管理不成体系。

借鉴形态：在 think stage 前加压力检查点；pruning 优先于摘要；摘要事件落 session 日志（依赖第 1 条）。

### 4. 文档从源码生成 + CI 保鲜门 ✅ 已实施（2026-08-22）

dsh 做法：`docs/subsystems/*` 的工具目录、事件 producer/consumer 图、API 段全部由 `scripts/gen-*.ts` 从源码 AST 生成，CI 有 freshness gate 检查文档与代码漂移。

Tlon 现状：已有 `scripts/check_*.py` 静态检查文化（架构 DAG、re-export、stage 纯度），`docs/reference/` 的工具清单、事件类型表靠手写同步。

借鉴形态：给 tools 目录和 `EventType` 枚举写生成脚本，输出到 reference 文档，CI 加 freshness 检查。零架构改动，与现有工程文化零摩擦。

**实施记录**：`scripts/gen_reference_docs.py`（AST 提取 `_assemble_cognitive` 注册序 + `Tool` 子类清单 + `EventType` 枚举），生成区块以 `<!-- BEGIN/END GENERATED -->` 标记嵌入 [reference/tools-reference.md](../reference/tools-reference.md)（注册清单）与 [reference/api-reference.md](../reference/api-reference.md)（事件枚举全集）；`--check` 为保鲜门，已接入 CI（`.github/workflows/unit-tests.yml`）并列入 AGENTS.md 验证八件套。手写叙事段落保持手写，只有易漂移的清单由源码生成。

## 中期，方向性借鉴（先讨论再动）

### 5. 工具管线水闸点 + waterfall dispatch

dsh 做法（`docs/tool-execution-pipeline.md`、`docs/cordis-primer.md`）：工具执行是挂满事件点的管线——`tool/call`（先落日志）→ `tools/pre-execute` waterfall（hooks/权限/沙箱策略）→ 单调 guard（只能 deny 或弃权）→ approval → `tools/execute` around waterfall（timeout/retry/metrics 包这层）→ 工具体 → `tools/post-execute`（可 accept/block/replace）→ `tools/result` 落日志。waterfall 是正式 dispatch 原语：listener 收 `(...args, next)`，可改写、可短路。

Tlon 现状：EventBus 只有 pub/sub；EffectExecutor 没有任何拦截点。

借鉴形态：自进化要求新机制能以数据/插件形式挂进系统而不是改引擎——**行为拦截点必须丰富**。在 EffectExecutor 的工具分发处引入可短路的有序 listener 链（不必搬整套 Cordis，先加一个 waterfall 语义的最小实现）。

### 6. 权限双旋钮：sandbox mode × approval policy，fail-closed

dsh 做法（`docs/subsystems/approval.md`）：sandbox mode（`read-only | workspace-write | danger-full-access`，只管文件效果，OS 级强制：Linux bwrap/Landlock、macOS Seatbelt）与 approval policy（`ask | never`）是两个独立旋钮，preset 打包成 UI 选择器；`ctx.approval` 结果是 closed union（`allowed-once | rejected | cancelled | unavailable`），缺席/不可答一律 deny；审批审计事件落日志但不进模型 transcript。

Tlon 现状：信任门只在原型 staging 层；成员跑 shell 工具没有执行层审批（有 DockerRuntime 沙箱）。

借鉴形态：给成员的 shell/filesystem 工具加策略旋钮。若加人工审批交互，dsh 的双向通道（WS 下行发问 + `POST /api/respond` 回填 + `RpcReceipt` 对迟到/重复应答回 `not-pending`）可参考——Tlon 选了 SSE（ADR 0005），审批应答走 REST 即可，不引 WebSocket。

### 7. Subagent 能力声明式 + 拒绝静默降级

dsh 做法（`docs/subsystems/subagent.md`）：`ctx.subagents` 是按名注册的多 provider 注册表；`SubagentCapabilities{outputSchema, depthLimit, toolFilter, persona}` 声明式能力，provider 没有的能力被请求就**启动即 loud 拒绝**，绝不静默降级。

Tlon 现状：spawn 有配额预算（max_activations / max_spawn_depth / session_spawn_quota），但没有能力协商。

借鉴形态：spawn 请求携带能力声明，注册表侧 fail-fast——与 Tlon 现有风格一致。

## 不适合搬 / 仅作参照

- **Cordis 全插件化 + 配置即数据**：dsh 的地基是「整棵插件树是 YAML 行、可按行 id patch」，与 Tlon 的 ServerApp 硬编码拓扑装配是对立面。改造成本巨大；但「装配描述数据化」与 Tlon 哲学暗合，可作远期方向，先记入本文不行动。
- **自反工具集**（`dsh-tool-cordis`：模型运行时自己写 Cordis 插件，并明说「sandbox 不是安全边界，当 bash 权限对待」）：路线选择与 Tlon 相反——Tlon 故意用受限 DSL 换安全。可吸收的点是 `cordis_inspect_*`：**agent 能自省自己跑在什么结构之上**。
- **Hooks 兼容 Claude Code/Codex 方言、MCP client、ACP bridge**：生态兼容层，Tlon 暂无对应需求。
- **Web UI 的 trajectory 视图**（token 级回放对话）：依赖第 1 条的事件溯源；有了它就是免费增量，单独做不值。
- **RPC 四象限协议**（POST 上行 + WS 下行 + respond 回填）：与 ADR 0005 的取舍相反，不搬；仅审批应答的 receipt 语义可参考（见第 6 条）。

## 来源

dsh 仓库内文档（2026-08-21 读取）：`docs/architecture.md`、`docs/cordis-primer.md`、`docs/capability-seams.md`、`docs/agent-lifecycle.md`、`docs/tool-execution-pipeline.md`、`docs/subsystems/{session,persistence,compaction,spill,approval,subagent,client-modules}.md`、`docs/api-gateway.md`、各 `packages/*/README.md`。
