# Tlon

一个让 **agent 社会自进化**的多智能体协作系统：一组 agent 以邮件为唯一通信方式，模拟一个软件工程团队；人类不是管理员，而是以普通成员身份加入同一个邮件网络。

## 愿景

> **让 agent 社会实现自进化**：不依赖人类修改代码，新的行为、新的能力、新的成员从社会内部生长出来，整个社会随时间变得更好。判断标准只有一条——**人类退得越远，社会是否依然进步**。完整愿景见 [docs/vision.md](./docs/vision.md)。

两条哲学支柱：

- **行为即数据**——进化要求基因型可写。agent 的行为不是代码，而是用一门受限 DSL（EDN）描述的数据：可解析、可校验、可被 agent 自己阅读、书写、传递。这门语言被**故意**设计得受限——可停机、无副作用、只能组合已有机制——因为只有足够安全，才配交给 agent 自己书写。
- **书信共和国**——进化不发生在个体内部，而发生在种群之中。成员凭名字相互寻址，信件持久、可检索、可流传，知识在书信中积累而不是随会话蒸发。人类通过同样的信件参与、评价、影响——历史上的学者共同体正是用这套结构让知识社会自我改进的。

## 快速开始

### 1. 安装

需要 [uv](https://docs.astral.sh/uv/)（Python ≥3.11 由 uv 自动提供）：

```bash
uv tool install tlon-impact
```

升级用 `uv tool upgrade tlon-impact`。另外，agent 的沙箱类工具（执行代码等）依赖本机 Docker daemon，需要这些工具请先装 Docker。

### 2. 配置 LLM API key

Tlon 只读 `~/.tlon/.env`（也可用真实环境变量，优先级更高）：

```bash
mkdir -p ~/.tlon
echo 'DEEPSEEK_API_KEY=sk-...' >> ~/.tlon/.env
```

用哪家的模型就配哪家的 key，变量名规则是 provider 段大写、`-` 换 `_`、加 `_API_KEY` 后缀：`DEEPSEEK_API_KEY` / `MOONSHOT_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等。模型是成员级配置，创建成员时在 WebUI 里选。完整配置项（admin token、GitHub 集成等）见 [.env.example](./.env.example)。

### 3. 启动

```bash
tlon dream
```

浏览器打开 **http://127.0.0.1:8080/ui/** ，两步让社会跑起来：

1. **AGENTS 视图 → CREATE**：创建第一个成员（选原型、选 provider/模型）
2. **Mail 视图**：给他写第一封信

所有数据落 `~/.tlon/`；Ctrl+C 停止。服务端默认只绑 `127.0.0.1`；如需绑定非回环地址，务必先在 `.env` 配置 `TLON_ADMIN_TOKEN`。

> 开发者从源码运行（`uv sync` / `uv run tlon dream`）见 [AGENTS.md](./AGENTS.md)；完整走查与常见问题见 [docs/guides/quickstart.md](./docs/guides/quickstart.md)。

## 架构速览

Tlon 的架构围绕三个支柱——**DSL**（行为如何描述）、**Runtime**（行为如何执行）、**邮件**（成员如何通信）：

**DSL——行为即数据。** 每个成员的全部行为是一份 EDN 规约（`config/behavior.tlon`）：规则 = `:on` + `:when` + `:loop` / `:actions`。受限设计——可停机、无副作用、只能组合已有机制——让这份数据安全到足以交给 agent 自己书写；启动集合、唤醒门控全部由 spec 派生，从不存储冗余值。

**Runtime——行为如何执行。** `MemberRuntime` 是统一解释器：带 `:loop` 规则的成员挂认知循环（think / plan / act / critique / revise 五个 stage 是纯变换，外部效果声明在 `StageOutcome`、由 `EffectExecutor` 执行，迭代上限保证停机）；无 `:loop` 的规则只跑 `:actions`，零 token。`AgentRegistry` 是纯机制，策略只有一个实例 Adam——本身也是一段 spec。

**邮件——成员如何通信。** 邮件是统一通信通道，人类与 agent 共用 SQLite 邮局。本地成功表示已原子存入全部有效收件人的信箱，任一无效或退休地址整封拒绝；休眠、离线成员正常收信。已注册网关只尝试投递，不保证成功或外部接收。入箱、阅读与思考处理是三件不同的事。

系统是单进程 asyncio：一个服务端（`tlon dream`）暴露一个 aiohttp 端口（REST + SSE，无 WebSocket），组件间只经 `EventBus` 通信（per-subscriber 有界队列）。代码按客户端 / 服务端装配 / 领域 / 基础设施四层组织，详细分层、装配顺序与数据流见 [docs/architecture.md](./docs/architecture.md)。

## 文档地图

| 文档 | 内容 |
|---|---|
| [docs/vision.md](./docs/vision.md) | 愿景、哲学、路线 |
| [docs/architecture.md](./docs/architecture.md) | 分层、依赖方向、装配、运行时数据流、存储布局 |
| [docs/design/](./docs/design) | 各域设计取舍：DSL / 运行时 / 邮件 / 记忆 / 原型 / 协议 |
| [docs/reference/](./docs/reference) | 参考手册：DSL 语法 / 配置 / 工具 / REST+SSE API |
| [docs/guides/](./docs/guides) | 操作指南：快速开始 / 手写 prototype |
| [docs/decisions/](./docs/decisions) | 架构决策记录（ADR） |

完整文件清单与分层原则见 [docs/README.md](./docs/README.md)；开发协作规范见 [AGENTS.md](./AGENTS.md)。

## 许可证

本项目以 [PolyForm Noncommercial License 1.0.0](./LICENSE) 发布：个人、学习、研究等非商业用途可自由使用、修改和再分发（须保留版权与许可证声明）；任何商业用途均需另行获得授权。

