# Tlon 开发指南

> 一个由观念构建的、正在入侵现实的宇宙

---

## 核心术语（博尔赫斯宇宙）

| 概念 | 命名 | 出处 |
|-----|------|------|
| 产品 | Tlon / 特隆 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| Chatroom | 代表大会 / Congress | 《代表大会》 |
| 记忆系统 | Shakespeare | 《与莎士比亚的记忆》 |
| ~~Skill 系统~~ | ~~巴别图书馆~~ | ~~《巴别图书馆》~~ |
| Git 工作流 | 花园 / Garden | 《小径分叉的花园》 |
| 自举计划 | 环形废墟 | 《环形废墟》 |
| Agent 管理 | Mirror / 镜 | 《镜子与面具》 |

---

## 快速开始

```bash
# 安装依赖
uv sync

# 配置环境
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# 运行（服务端 + Operator TUI）
uv run tlon server    # 终端 1
uv run tlon ops       # 终端 2（Operator TUI，含 mail 视图——人类邮件主界面）

# 单元测试
uv run pytest
```

**文学化命令别名**：`server` = `dream`, `tui` = `intrude`, `wizard` = `mirror`

---

## 文档索引

| 领域 | 设计文档 | 架构文档 |
|------|----------|----------|
| 概述 | [design/README.md](./docs/design/README.md) | [architecture/README.md](./docs/architecture/README.md) |
| **通信协议** | [design/client-server-protocol.md](./docs/design/client-server-protocol.md) | - |
| 用户指南 | [user/README.md](./docs/user/README.md) | - |
| 架构边界 | - | [explicit-boundaries.md](./docs/architecture/explicit-boundaries.md) |
| 配置系统 | [design/config.md](./docs/design/config.md) | [architecture/domains/base.md](./docs/architecture/domains/base.md) |
| **文件系统布局** | **[design/config.md](./docs/design/config.md)** (含 ~/.tlon/ 目录结构) | - |
| Agent 系统 | [design/agents.md](./docs/design/agents.md), [narrative-prompt.md](./docs/design/narrative-prompt.md), [async-agent-loop.md](./docs/design/async-agent-loop.md), **[dsl-agents.md](./docs/design/dsl-agents.md)** 🆕 | [architecture/domains/agents.md](./docs/architecture/domains/agents.md) |
| 记忆系统 | [design/memory.md](./docs/design/memory.md) | - |
| 消息系统 | **[design/mail.md](./docs/design/mail.md)** 📮 | - |
| Member 与亚当 | **[design/members.md](./docs/design/members.md)** | - |
| **TUI 设计** | **[Operator](./docs/design/tui-operator.md)**（Congress 已退役） | - |

---

## 状态总览

| 领域 | 状态 | 评分 | 备注 |
|------|------|------|------|
| 配置系统 | ✅ 已验证 | 100/100 | 稳定 |
| Agent 系统 | ✅ 已验证 | 95/100 | 异步多 Session、步进调度 |
| 记忆系统 | ✅ 已验证 | 100/100 | 稳定 |
| 消息系统 | ✅ 已验证 | 95/100 | 邮件模型 R2 已切换（mail.md），PR-A/B/C 全部落地 |
| Client-Server | ✅ 已验证 | 95/100 | Trace 系统、Event 扩展已完成 |
| Metrics 支柱 | ✅ 已验证 | 95/100 | MetricsService 事件消费 + GET /metrics + operator metrics 视图；真实冒烟通过 |
| ~~Skill 系统~~ | ❌ 已移除 | - | 2026-03-17 移除 |
| ~~TUI (Congress)~~ | ❌ 已退役 | - | 2026-07 退役（PR-C），由 Operator mail 视图取代 |
| TUI (Operator) | ⚠️ 待验证 | 80/100 | TypeScript/Ink 实现（apps/operator），真实连接服务端，mail 视图已落地 |
| Sandbox | ✅ 已验证 | 95/100 | 共享 DockerRuntime, macOS 支持 |

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
- 大活派 subagent：brief 给精确的文件:行号、已核实的现状事实、已定稿决策、测试要求、验证命令；超时/中断用 resume 续跑；完成后必须自己复核（四件套 + 审 diff + 真实冒烟），不盲信汇报
- 验证四件套：`uv run pytest src/tests/ -q`、`uv run ruff check src/`、`uv run python scripts/check_architecture.py`、`uv run python scripts/check_no_reexport.py`，全绿才算完
- 真实冒烟：起 dream + SDK 发邮件 + 查 `~/.tlon` 收件箱验证；完事清理冒烟痕迹、释放 8080/8888 端口

### 质量准则
- 既有测试是行为等价性套件：重构时一个不许红；过期测试经用户授权可删，逐条列出说明
- 文档与代码同步：设计文档随代码更新；历史达成情况保留，被废止的决策用删除线或新决策覆盖
- 诚实优先：review 给证据说话；发现用户指令有问题时摆数据提出，由用户定夺

### 沟通
- 汇报带实质：改动清单、测试数、四件套结果、与计划的偏差及原因
- 未完成/未验证的不说成完成；过程事故（subagent 超时、替换误伤等）如实记录

---

## 参考链接

| 类型 | 文档 |
|------|------|
| 开发规范 | [开发流程](./docs/design/development-process.md), [代码风格](./docs/design/code-style.md) |
| 架构 | [显式边界](./docs/architecture/explicit-boundaries.md), [agents 域](./docs/architecture/domains/agents.md), [base 域](./docs/architecture/domains/base.md) |
| 速查 | [导入路径速查](./docs/reference/imports.md) |

---

*最后更新：2026-08-06*
