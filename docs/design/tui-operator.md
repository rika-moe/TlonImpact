# Tlon Operator - 人类操作台

> 状态: ✅ 已验证
> 最后验证: 2026-08-13
> 实现: `apps/operator/`（TypeScript + Ink，Node ≥ 22）
> 协议: HTTP + SSE 单端口 v2（见 [client-server-protocol.md](./client-server-protocol.md)）

**定位**：人类操作台——以 mail 视图为主界面（人类的邮件收发入口），外加 Agents / Events / Traces / Config / Metrics 五个监控视图。本文档描述 `apps/operator` 的当前实现，与代码同步。

---

## 1. 启动

```bash
uv run tlon ops
```

`tlon ops` 定位 `apps/operator/`：优先运行构建产物 `dist/cli.js`，否则回退到 tsx dev 模式（`src/tlon/app/launcher/commands/ops.py:46-64`）；连接参数通过命令行透传（`ops.py:96-104`）。

**CLI 参数与环境变量**（`apps/operator/src/args.ts:9-26`）：

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `--host` | `TLON_HOST` | `127.0.0.1` | 服务端地址 |
| `--rest-port` / `-r` | `TLON_REST_PORT` | `8080` | HTTP API 端口 |
| `--username` / `-u` | `TLON_USERNAME` | `guest` | 邮件身份（`{username}@tlon.internal`） |

也支持 `--host=x` 等号形式；`--help` 打印用法。

---

## 2. 连接协议

Operator 是 HTTP+SSE 单端口 v2 协议的 TUI 客户端（`apps/operator/src/client/sseClient.ts`），协议全貌见 [client-server-protocol.md](./client-server-protocol.md)，本节只记客户端行为。

**会话流程**：

1. `POST /sessions`——声明 `username`、`client_type=tui`、`mode`、`filters`、`protocol_version=2.0.0`；返回 `session_id` + agents/participants 快照（作为 `agents`/`participants` 事件分发）（`sseClient.ts:134-161`）
2. `GET /events?session_id=...`——打开 SSE 流；`data:` 帧携带 `{"type":"event", event_type, payload, source, timestamp, trace_id, span_id}`，`: ping` 注释行是服务端心跳（`sseClient.ts:163-217`）
3. `PATCH /sessions/{id}`——切换 mode；会话建立后 App 立即切到 `"event"` 模式，让服务端推送全部事件类型（`App.tsx:38-42`，`sseClient.ts:79-95`）
4. `DELETE /sessions/{id}`——`dispose()` 时 best-effort 显式断开（服务端幂等）（`sseClient.ts:97-115`）

**断线重连**（`sseClient.ts:219-234`）：指数退避 1s → 30s 封顶（无 jitter，`backoffDelay`）；重连即从零重建会话——新 session_id、新快照，**中断期间的事件丢失**（与 WS 时代语义一致）。

**REST 端点**（`apps/operator/src/client/restClient.ts:73-156`）：`GET /health`、`GET /config`、`GET /agents`、`GET /agents/{id}`、`GET /traces`、`GET /traces/{id}`、`GET /admin/config/agents`、`GET /inbox?username&limit`、`POST /messages`（同步回执：成功返回 `mail_id`，4xx 携带 `error`/`reason`）、`GET /participants`、`GET /metrics`。

---

## 3. 布局与全局键位

**布局**：单行 Header + 内容区 + 底部提示行（`App.tsx:76-98`）。Header 左侧是视图页签 `1:Mail … 6:Metrics`，右侧是 `username@tlon.internal`、`host:port`、连接状态 `●`（绿 connected / 黄 connecting·reconnecting / 红 disconnected）（`components/Header.tsx:44-62`）。底部固定提示 `1-6 / tab / ←→ switch view · q quit`。

**全局键位**（`App.tsx:53-72`）：

| 键 | 功能 |
|----|------|
| `1`-`6` | 切到对应视图 |
| `tab` / `→` | 下一视图 |
| `←` | 上一视图 |
| `q` / `Ctrl+C` | 退出 |

mail 写信/回复表单编辑期间全局键被屏蔽（表单独占键盘；`Ctrl+C` 除外）（`App.tsx:27-28, 58`）。`r` 不是全局刷新——各视图自定义。

**视图清单**（`types.ts:3-5`；默认视图是 mail，`App.tsx:25`）：Mail、Agents、Events、Traces、Config、Metrics。除 Metrics 外各视图为"左列表 + 右详情"两栏（共享 `components/ScrollList.tsx` 的 `ScrollList`/`JsonDetail`）。

---

## 4. Mail 视图（默认视图）

`views/MailView.tsx`——人类的邮件主界面（设计见 [mail.md](./mail.md)）。

- **线程列表 + 会话窗格**：左栏按 `thread_id` 分组线程、最新在前（显示参与者、主题/内容摘要、邮件数）；右栏按时间序显示选中线程的邮件（`mail/threads.ts` 的 `groupByThread`）
- **初始加载**：`GET /inbox` + `GET /participants`（地址簿）（`MailView.tsx:82-102`）
- **实时合并**：SSE `mail.delivered` 事件按 mail id 去重合并（服务端每个收件人发一条事件）；自己发的信不会投递回来，在 `POST /messages` 同步 ack 时以真实 id 上屏（`MailView.tsx:105-118, 196-213`）
- **写信 / 回复**：四字段表单 To / Cc / Subject / Content；回复预填 To 与 `Re:` 主题，携带 `in_reply_to` / `thread_id`；To/Cc 字段下方显示地址簿候选（`MailView.tsx:266-295`）
- **发送**：`POST /messages` 同步回执——成功即上屏关闭表单；失败时表单保留、服务端错误落回表单（`MailView.tsx:178-218`）

**键位**（`MailView.tsx:220-264`）：

| 场景 | 键 | 功能 |
|------|----|------|
| 浏览 | `↑` / `↓` | 选线程 |
| 浏览 | `c` | 写信 |
| 浏览 | `r` | 回复选中线程 |
| 表单 | `enter` | 下一字段；Content 上提交 |
| 表单 | `↑` / `↓` | 切字段 |
| 表单 | `esc` | 取消 |

---

## 5. Agents 视图

`views/AgentsView.tsx`——Agent 平铺列表 + raw JSON 详情。

- 列表项为 `id name [state]`，按状态着色（running/active/idle/online 绿，stopped/offline/error/failed 红，其余黄）（`AgentsView.tsx:10-22`）
- 数据来自 SSE 会话快照；任何 `agent*` 事件触发 `GET /agents` 重载（`AgentsView.tsx:46-63`）
- 右栏默认显示选中 Agent 的列表项 JSON；`enter` 拉 `GET /agents/{id}` 完整详情（`AgentsView.tsx:74-83`）

**键位**（`AgentsView.tsx:65-84`）：`↑`/`↓` 选择 · `enter` 详情 · `r` 刷新。

---

## 6. Events 视图

`views/EventsView.tsx`——SSE 事件流。

- 列表行显示 时间（本地接收时刻）/ eventType / source / traceId 前 8 位；右栏 raw JSON（`EventsView.tsx:73-95`）
- 缓冲上限 500 条，超出从头裁（`EventsView.tsx:7, 24-28`）
- 默认自动跟随最新事件；向上滚动即暂停跟随，回到底部恢复（`EventsView.tsx:36-38, 40-49`）

**键位**（`EventsView.tsx:40-56`）：`↑`/`↓` 滚动 · `c` 清空 · `p` 暂停/恢复。

---

## 7. Traces 视图

`views/TracesView.tsx`——Trace 列表 + raw JSON 详情。

- 列表来自 `GET /traces?limit=100`，行显示 trace_id 及可用的 name/source/status/root_agent 摘要（`TracesView.tsx:12-20, 29-38`）
- `enter` 拉 `GET /traces/{id}` 完整 trace JSON

**键位**（`TracesView.tsx:44-62`）：`↑`/`↓` 选择 · `enter` 详情 · `r` 刷新。

---

## 8. Config 视图（只读）

`views/ConfigView.tsx`——**只读**配置展示，上下两块 raw JSON：`GET /config`（服务端配置）与 `GET /admin/config/agents`（Agent 配置）（`ConfigView.tsx:6-7, 14-23`）。无编辑能力。

**键位**：`r` 刷新（`ConfigView.tsx:29-31`）。

---

## 9. Metrics 视图

`views/MetricsView.tsx`——`GET /metrics` 的服务端进程内指标快照，每 2s 轮询（唯一轮询视图），按分组展示（`metrics/snapshot.ts` 的 `groupMetrics`）（`MetricsView.tsx:7-8, 24-28`）。

**键位**：`r` 手动刷新（`MetricsView.tsx:30-32`）。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [client-server-protocol.md](./client-server-protocol.md) | HTTP+SSE v2 协议全貌 | L1 |
| [mail.md](./mail.md) | 邮件模型设计 | L1 |

---

## 验证清单

- [x] 视图清单、键位与 `apps/operator/src/` 一致（六视图，默认 mail）
- [x] 协议描述与 `sseClient.ts` / `restClient.ts` 一致（HTTP+SSE v2，无 WS）
- [x] CLI 参数与环境变量与 `args.ts` 一致
- [x] 无愿景/未实现功能描述

---

*更新时间: 2026-08-13*
