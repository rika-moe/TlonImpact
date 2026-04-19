# 里程碑 7: Client 扩展与可观测性

> 状态: 🔄 进行中 (Phase 2)
> 实际开始: 2026-03-18
> 计划完成: 2026-04
> 负责人: 架构师 + 开发团队

---

## 目标

1. 支持多 Client 接入（Terminal/Web/Telegram）
2. 实现全链路可观测性（Tracing + Agent 日志）
3. 建立协议版本化机制
4. 搭建自动化测试基础设施

---

## 背景与痛点

当前已实现 Terminal (TUI) Client，但存在以下问题：

| 痛点 | 影响 | 解决方案 |
|------|------|----------|
| 无 CI、无单元测试、无 lint | 代码质量风险 | Task 1.1: CI/CD 基础 |
| 无集成测试 | 每次变更需手动测试 | Task 1.3: Test Mode + 集成测试 |
| Log 不全，无法追踪 Agent 工具调用 | 调试困难 | Task 1.2: Event 扩展 + Trace 系统 |
| 仅支持 Terminal | Web/IM 用户无法接入 | Task 3.2 + 3.3: Web/Telegram Client |

---

## 交付物

### Phase 1: 基础架构（Week 1）✅ 已完成

- [x] GitHub Actions CI（lint + type check + unit test）
- [x] Agent 系统重构（解耦 + 直接调用）
- [x] Event 扩展（trace_id, span_id, 新增事件类型）
- [x] TraceStore 实现（文件存储）
- [x] TUI Event 模式增强（按 trace_id 过滤）

### Phase 2: 协议与 SDK（Week 2）🔄 进行中

- [ ] 协议版本协商机制
- [ ] SDK 重构（BaseClient + Adapter 模式）
- [x] REST API 扩展（/traces）
- [ ] REST API 扩展（/agents/{id}/logs, /test/*）

### Phase 3: Client 实现（Week 3-4）

- [ ] TUI 迁移到新 SDK
- [ ] React Web Client（ChatRoom, AgentPanel, TraceViewer）
- [ ] Telegram Bot（命令支持，消息转发）

---

## 架构要点

```
Client Ecosystem
├── Terminal (TUI)
│   └── TerminalAdapter ──► WebSocket ──► ClientGateway
├── Web (React)
│   └── WebAdapter ───────► WebSocket ──► ClientGateway
└── Telegram Bot
    └── TelegramAdapter ──► WebSocket ──► ClientGateway

可观测性
├── TraceStore (trace-xxx.jsonl)
├── AgentLogger (stdout.log, stderr.log)
└── EventBus (增强事件: thinking_start, llm_request, tool_execute_start)
```

---

## 任务分解

### Phase 1: 基础架构

#### Task 1.1: CI/CD 基础 ✅ 已完成
- **负责人**: DevOps Agent
- **工期**: 1 人天
- **完成日期**: 2026-03-18
- **任务**:
  - 配置 GitHub Actions（`.github/workflows/ci.yml`）
  - 配置 ruff（lint + format）
  - 配置 mypy（type check）
  - 配置 pytest（unit test）
- **验收标准**: ✅ 已验证
  ```bash
  ruff check src/ && ruff format --check src/
  mypy src/tlon/
  pytest tests/unit/ -xvs
  ```

#### Task 1.2: Event 扩展 ✅ 已完成
- **负责人**: Backend Agent
- **工期**: 2 人天
- **状态**: 已完成
- **已完成**:
  - ✅ ~~EventBus 化工具执行~~（ ）→ 2026-04-19 再次重构为直接调用（）
  - ✅ ~~显式状态机 ReAct Loop~~（）→ 2026-04-19 简化为内联循环（）
  - ✅ Agent 解耦：PromptContext + 组件注入化（ ）
  - ✅ 扩展 `Event` 类，添加 `trace_id`, `span_id`, `parent_span_id`
  - ✅ 新增 EventType：`AGENT_THINKING_START/END`, `AGENT_LLM_REQUEST/RESPONSE`, `TOOL_EXECUTE_START/END`
  - ✅ 埋点：MessageRouter, Agent.think(), Agent._execute_tool_call()
- **验收标准**: ✅ TUI Event 模式可见 trace_id 链路

#### Task 1.3: Trace 基础 ✅ 已完成
- **负责人**: Backend Agent
- **工期**: 2 人天
- **依赖**: Task 1.2
- **状态**: 已完成
- **已完成**:
  - ✅ `TraceStore` 接口与文件实现（`FileTraceStore`）
  - ✅ REST API (`/traces/{trace_id}`)
  - ✅ EventBus 集成（`start_span` 上下文管理器）
  - ✅ MessageRouter 自动记录 span
- **待完成**:
  - TUI 新增 `/trace <trace_id>` 命令
- **验收标准**:
  ```bash
  curl http://localhost:8080/traces/trace-xxx
  # 返回完整追踪树
  ```

### Phase 2: 协议与 SDK

#### Task 2.1: 协议版本化
- **负责人**: Backend Agent
- **工期**: 2 人天
- **依赖**: Task 1.1
- **任务**:
  - WebSocket `auth` 消息扩展（`protocol_version`, `supported_versions`）
  - `ClientGateway` 版本协商逻辑
  - 向后兼容（旧 Client 默认使用 1.0.0）
- **验收标准**: 新旧 Client 可同时连接

#### Task 2.2: SDK 重构
- **负责人**: SDK Agent
- **工期**: 3 人天
- **依赖**: Task 2.1
- **任务**:
  - `BaseClient` 抽象接口
  - `TerminalAdapter` 实现
  - `WebAdapter` 实现
  - `TlonClient` 统一入口
- **验收标准**:
  ```python
  async with TlonClient("ws://localhost:8888/ws", adapter=TerminalAdapter()) as client:
      await client.authenticate()
      await client.send_message("Hello")
  ```

#### Task 2.3: REST API 扩展
- **负责人**: Backend Agent
- **工期**: 1 人天
- **依赖**: Task 1.3
- **任务**:
  - `/traces/{trace_id}` GET
  - `/traces` GET（查询列表）
  - `/agents/{id}/logs` GET
  - `/test/*` 测试专用端点（test_mode 下可用）
- **验收标准**: REST API 文档更新，集成测试可用

### Phase 3: Client 实现

#### Task 3.1: TUI 适配
- **负责人**: TUI Agent
- **工期**: 2 人天
- **依赖**: Task 2.2
- **任务**:
  - TUI 迁移到新 SDK
  - Event 模式增强（按 trace_id 过滤）
  - 新增 `/trace <trace_id>` 命令
- **验收标准**: TUI 功能与之前一致，可查看 trace

#### Task 3.2: Web Client
- **负责人**: Frontend Agent
- **工期**: 5 人天
- **依赖**: Task 2.2, Task 2.3
- **任务**:
  - React 项目初始化（TypeScript）
  - `useWebSocket`, `useTlonClient` hooks
  - `TlonClient` TypeScript SDK
  - 组件：ChatRoom, AgentPanel, TraceViewer
- **验收标准**: Web Client 可同时与 TUI 连接，收发消息

#### Task 3.3: Telegram Bot
- **负责人**: Bot Agent
- **工期**: 3 人天
- **依赖**: Task 2.2
- **任务**:
  - `TelegramAdapter` 实现
  - Bot 命令：`/start`, `/status`, `/agents`
  - 消息转发：Telegram ↔ Tlon Server
- **验收标准**: Telegram 消息可送达 Tlon，Agent 回复可送达 Telegram

---

## 时间线

```
Week 1: Phase 1 - 基础架构
┌─────────────────────────────────────────────────────────────┐
│ Mon    Tue    Wed    Thu    Fri    Sat    Sun              │
│ 1.1    1.2    1.2    1.3    1.3    ──     ──               │
│ CI/CD  Event  Event  Trace  Trace  休息   休息              │
│ 基础   扩展   扩展   基础   基础                            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Milestone 1    │
                    │  可观测基础完成  │
                    └─────────────────┘

Week 2: Phase 2 - 协议与 SDK
┌─────────────────────────────────────────────────────────────┐
│ Mon    Tue    Wed    Thu    Fri    Sat    Sun              │
│ 1.x    2.1    2.1    2.2    2.2    ──     ──               │
│ 收尾   协议   协议   SDK    SDK    休息   休息              │
│        版本化 版本化 重构    重构                           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Milestone 2    │
                    │  协议标准化完成  │
                    └─────────────────┘

Week 3-4: Phase 3 - Client 实现
┌─────────────────────────────────────────────────────────────┐
│ Mon    Tue    Wed    Thu    Fri    Sat    Sun              │
│ 2.3    2.x    3.1    3.2    3.2    ──     ──               │
│ REST   收尾   TUI    Web    Web    休息   休息              │
│ 扩展   阶段   适配   Client Client                          │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Mon    Tue    Wed    Thu    Fri    Sat    Sun              │
│ 3.2    3.3    3.3    3.x    测试   ──     ──               │
│ Web    Bot   Bot    收尾   集成   休息   休息              │
│ Client 开发  开发   阶段   测试                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Milestone 3    │
                    │  多 Client 支持  │
                    └─────────────────┘
```

---

## 里程碑

| 里程碑 | 日期 | 状态 | 验收标准 |
|--------|------|------|----------|
| M1: 可观测基础 | Week 1 周五 | ✅ 已完成 | 1. CI 通过 ✅<br>2. Event 扩展完成 ✅<br>3. Trace 基础可用 ✅ |
| M2: 协议标准化 | Week 2 周五 | 🔄 进行中 | 1. 版本协商工作<br>2. SDK 重构完成<br>3. REST API 扩展 ✅ |
| M3: 多 Client 支持 | Week 4 周五 | ⏳ 待开始 | 1. TUI 适配完成<br>2. Web Client 可用<br>3. Telegram Bot 可用 |
| M4: 集成测试 | Week 4 周日 | ⏳ 待开始 | 1. 集成测试通过<br>2. 文档完成 |

---

## 资源需求

| 角色 | 负责任务 | 时间投入 |
|------|----------|----------|
| Backend Agent | 1.2, 1.3, 2.1, 2.3 | Week 1-2 全职 |
| SDK Agent | 2.2, 3.1 | Week 2-3 全职 |
| Frontend Agent | 3.2 | Week 3-4 全职 |
| Bot Agent | 3.3 | Week 4 全职 |
| DevOps Agent | 1.1, CI/CD 维护 | Week 1 全职 + 后续维护 |

---

## 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| Web Client 工作量超预期 | 中 | 高 | Week 3 中期评估，必要时缩减功能（先实现只读模式） |
| Telegram Bot 审核延迟 | 低 | 中 | 使用测试 Bot Token，不等待官方审核 |
| 协议版本兼容性复杂 | 中 | 中 | Week 2 中期代码审查，必要时简化版本协商 |
| Agent 日志捕获困难 | 中 | 中 | 备选方案：通过 EventBus 转发日志事件 |

---

## 依赖关系

```
Task 1.1 (CI/CD) ✅
    │
Task 1.2 (Event 扩展) 🔄
    │
    ├──► Task 1.3 (Trace 基础)
    │         │
    │         ▼
    │    Task 2.3 (REST 扩展)
    │
    ▼
Task 2.1 (协议版本化)
    │
    ├──► Task 2.2 (SDK 重构)
    │        │
    │        ├──► Task 3.1 (TUI 适配)
    │        ├──► Task 3.2 (Web Client) ──► Task 2.3 (REST 扩展)
    │        └──► Task 3.3 (Telegram Bot)
```

---

## 文档索引

| 文档 | 目的 |
|------|------|
| [design/client-server-protocol.md](../design/client-server-protocol.md) | 协议规范详情 |
| [architecture/client-server.md](../architecture/client-server.md) | 架构设计详情 |

---

## 验证清单

- [x] CI/CD 通过（lint + type check + unit test）
- [x] Agent 系统重构完成（解耦 + 直接调用）
- [x] Event 扩展实现（trace_id, span_id）
- [x] Trace 系统可用（创建、查询、展示）
- [ ] 协议版本协商工作
- [ ] SDK 重构完成（BaseClient + Adapters）
- [x] REST API 扩展（/traces）
- [ ] TUI 适配新 SDK
- [ ] Web Client 可用（收发消息、查看状态）
- [ ] Telegram Bot 可用（消息转发）
- [ ] 集成测试通过
- [ ] 文档更新完成

---

*计划制定时间: 2026-03-18*
*最后更新: 2026-04-19*
