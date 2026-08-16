# Tlon

一个让 **agent 社会自进化**的多智能体协作系统：一组 agent 以邮件为唯一通信方式，模拟一个软件工程团队；人类不是管理员，而是以普通成员身份加入同一个邮件网络。

## 愿景

> **让 agent 社会实现自进化**：不依赖人类修改代码，新的行为、新的能力、新的成员从社会内部生长出来，整个社会随时间变得更好。判断标准只有一条——**人类退得越远，社会是否依然进步**。完整愿景见 `docs/vision.md`。

两条哲学支柱：

- **行为即数据**——进化要求基因型可写。agent 的行为不是代码，而是用一门受限 DSL（EDN）描述的数据：可解析、可校验、可被 agent 自己阅读、书写、传递。这门语言被**故意**设计得受限——可停机、无副作用、只能组合已有机制——因为只有足够安全，才配交给 agent 自己书写。
- **书信共和国**——进化不发生在个体内部，而发生在种群之中。成员凭名字相互寻址，信件持久、可检索、可流传，知识在书信中积累而不是随会话蒸发。人类通过同样的信件参与、评价、影响——历史上的学者共同体正是用这套结构让知识社会自我改进的。

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境（首次使用）
mkdir -p ~/.tlon && cp .env.example ~/.tlon/.env
# 编辑 ~/.tlon/.env，填入 OPENAI_API_KEY（或直接用真实环境变量，.env 仅作便捷层）

# 3. 运行——两个终端
uv run tlon dream    # 终端 1：服务端（REST + SSE，默认 127.0.0.1:8080）
                     # 空社会首次启动自动从职位原型初始化标准团队（幂等）
uv run tlon ops      # 终端 2：Operator TUI（人类邮件主界面 + 成员/原型管理）

# 4. 发第一封信：在 TUI 的 Mail 视图按 c，给团队某成员（如「顾言蹊」）写信
```

完整走查（含常见问题排查）见 `docs/guides/quickstart.md`。

## 架构速览

Tlon 的架构围绕三个支柱——**DSL**（行为如何描述）、**Runtime**（行为如何执行）、**邮件**（成员如何通信）：

**DSL——行为即数据。** 每个成员的全部行为是一份 EDN 规约（`config/behavior.tlon`）：规则 = `:on` + `:when` + `:loop` / `:actions`。受限设计——可停机、无副作用、只能组合已有机制——让这份数据安全到足以交给 agent 自己书写；认知型/确定型、启动集合、唤醒门控全部由 spec 派生，从不存储冗余值。

**Runtime——行为如何执行。** `MemberRuntime` 是统一解释器：认知型成员挂认知循环（think / plan / act / critique / revise 五个 stage 是纯变换，外部效果声明在 `StageOutcome`、由 `EffectExecutor` 执行，迭代上限保证停机）；确定型成员只跑 `:actions`，零 token。`AgentRegistry` 是纯机制，策略只有一个实例 Adam——本身也是一段 spec。

**邮件——成员如何通信。** 邮件是唯一通道：agent 发信只有一个出口（`sendEmail` 工具），人类以普通成员身份加入同一网络。邮箱是磁盘文件（`inbox.jsonl` / `sent.jsonl` / `inbox.cursor`），先持久化、后处理；`all@tlon.internal` 广播、退信、离线等待都是书信共和国的机制。

系统是单进程 asyncio：一个服务端（`tlon dream`）暴露一个 aiohttp 端口（REST + SSE，无 WebSocket），组件间只经 `EventBus` 通信（per-subscriber 有界队列）。代码按客户端 / 服务端装配 / 领域 / 基础设施四层组织，详细分层、装配顺序与数据流见 `docs/architecture.md`。

## 文档地图

| 文档 | 内容 |
|---|---|
| `docs/vision.md` | 愿景、哲学、路线 |
| `docs/architecture.md` | 分层、依赖方向、装配、运行时数据流、存储布局 |
| `docs/design/` | 各域设计取舍：DSL / 运行时 / 邮件 / 记忆 / 原型 / 协议 |
| `docs/reference/` | 参考手册：DSL 语法 / 配置 / 工具 / REST+SSE API |
| `docs/guides/` | 操作指南：快速开始 / 手写 prototype |
| `docs/decisions/` | 架构决策记录（ADR） |

完整文件清单与分层原则见 `docs/README.md`；开发协作规范见 `AGENTS.md`。

