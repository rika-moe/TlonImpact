# WebUI 设计（apps/webui）

> 决策已于 2026-08-21 拍板：技术栈 React + Vite + TS；由 `tlon dream` 静态托管构建产物（单端口）；第一版范围为 TUI 全量对齐 + 新增 Logs 视图。

WebUI 是 Tlon 的第二个官方人类客户端，与 [apps/operator](../../apps/operator)（Ink TUI）平级：同一份 REST + SSE 契约（协议 v2，见 [server-protocol.md](./server-protocol.md)），不改服务端协议面。视觉风格为 cyberpunk（暗黑底 + 霓虹描边 + glow）。

## 定位与原则

- **纯客户端**：只经 REST + SSE 与服务端通信，和 TUI、curl、第三方客户端同为一等公民。服务端唯一的改动是静态托管构建产物（见下文「托管」）。
- **与 TUI 功能对齐再增量**：七个 TUI 视图全部覆盖；增量只有 Logs 视图（消费 TUI 未用的 `GET /logs` / `GET /agents/{id}/logs`）。Config 与 Settings 合并为一个 SETTINGS 页。
- **复用 TUI 的纯逻辑**：`mail/threads.ts`（线程分组）、`metrics/snapshot.ts`（指标分组格式化）是纯 TS 模块，直接拷贝进 webui；渲染层（ink → DOM）与 SSE 传输层（Node fetch 流 → 浏览器 `EventSource`）重写。不做跨 app 的共享包——为两个小模块引入 workspace 复杂度不值。

## 目录结构

```
apps/webui/
├── package.json          # react 18 + react-dom + vite + typescript
├── vite.config.ts        # dev proxy → 127.0.0.1:8080；build outDir: dist
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx           # 侧边导航 + 路由（无 react-router，自管 view state）
│   ├── client/
│   │   ├── rest.ts       # fetch 薄封装，端点与 operator restClient.ts 一一对应
│   │   └── sse.ts        # POST /sessions → PATCH mode=event → EventSource；指数退避重连
│   ├── lib/
│   │   ├── threads.ts    # 拷自 operator
│   │   └── snapshot.ts   # 拷自 operator
│   ├── styles/
│   │   ├── tokens.css    # cyberpunk 设计 token（CSS 变量）
│   │   └── components.css# 面板/按钮/徽章/终端面板等基础组件类
│   └── views/
│       ├── MailView.tsx  AgentsView.tsx  EventsView.tsx  TracesView.tsx
│       ├── MetricsView.tsx  LogsView.tsx  SettingsView.tsx
└── test/                 # 纯逻辑模块测试（tsx --test，与 operator 同）
```

状态管理不用库：App 持有 rest/sse 客户端实例，view 各自 `useState`，与 operator 的结构一致。

## 页面规划

| 页面 | 对应 TUI 视图 | 功能 | 端点 / 事件 |
|---|---|---|---|
| MAIL | mail | 三栏：线程列表（未读 ●）｜会话内容｜写信抽屉（To/Cc 补全、回复预填 in_reply_to/thread_id） | `GET /inbox`、`GET /participants`、`POST /messages`、SSE `mail.delivered` |
| AGENTS | agents | 成员卡片网格（状态着色）+ 详情、创建（选原型→表单）、编辑、删除 | `GET /agents[/{id}]`、`/admin/config/agents` 增删改、`GET /admin/prototypes`（创建向导） |
| EVENTS | events | 终端面板式全量事件流、暂停/清空、展开 JSON | 纯 SSE |
| TRACES | traces | trace 列表 + span 详情 | `GET /traces[/{id}]` |
| METRICS | metrics | 指标仪表盘，按 mail/agent/llm/tool 分组，2s 轮询 | `GET /metrics` |
| LOGS | —（新增） | 日志流，level/agent/trace 过滤 | `GET /logs`、`GET /agents/{id}/logs` |
| SETTINGS | config + settings | 服务端配置只读区 + LLM env 编辑（base_url/api_key/model_name/temperature） | `GET /config`、`GET /admin/config/agents`、`GET/PUT /admin/settings/env` |
| PROTOTYPES | —（新增） | 原型库管理：正式库/ staging 双区列表、详情（元数据 + parameters + behavior.tlon 全文）、手写创建、编辑、删除、staging 审批 | 见下文「原型管理 API」 |

staging 原型审批区从 AGENTS 视图迁至 PROTOTYPES 视图（2026-08-21 拍板：原型管理是独立职责）。

## 原型管理 API（2026-08-21 拍板：元数据、详情、审批、编辑、增删查改）

原型 = 目录（`behavior.tlon` + `prototype.edn` 元数据），信任分级不变：staging（LLM 写，`:trusted false`）→ 人工 approve → 正式库；**人类手写直接进正式库**（handwritten implies trusted，与既有语义一致）。写路径一律先过校验链（`analyze` 模板分析 → EDN parse → `validate_member_spec` schema），校验失败不落盘。

在既有 `GET /admin/prototypes`、`GET /admin/prototypes/staging`、`POST .../staging/{name}/approve|reject` 之上新增（域函数在 [agents/prototypes.py](../../src/tlon/agents/prototypes.py)，路由在 `rest/admin_prototypes.py` 管理面）：

| 端点 | 语义 | 响应 / 错误 |
|---|---|---|
| `GET /admin/prototypes/{name}` | 正式库原型详情：元数据 + `parameters` + `behavior`（behavior.tlon 全文） | 404 `prototype_not_found` |
| `GET /admin/prototypes/staging/{name}` | staging 原型详情，同上（trusted=false） | 同上 |
| `POST /admin/prototypes` | 人类手写创建，直接进正式库（trusted=true）；body `{name, behavior, display_name?, description?}` | 201；校验失败 400 `invalid_prototype` |
| `PATCH /admin/prototypes/{name}` | 更新元数据（display_name/description）和/或 behavior（重跑校验链，失败不落盘） | 200 / 400 / 404 |
| `PATCH /admin/prototypes/staging/{name}` | 审批前编辑 LLM 产物，同上 | 同上 |
| `DELETE /admin/prototypes/{name}` | 删除正式库原型（staging 的删除即已有 reject） | 200 / 404 |

两个 GET 列表端点的响应顺带补上 `parameters`（纯增量字段，不破坏现有客户端）。

`PROTOTYPES` 视图：左栏双区列表（TRUSTED LIBRARY 青卡片 / STAGING 黄描边卡片），右侧详情面板（元信息 + parameters 徽章 + behavior.tlon 终端面板全文）；操作：NEW（手写表单，behavior 大文本域）、EDIT（元数据 + behavior）、DELETE（确认框，品红）、staging 的 APPROVE（青）/ REJECT（品红）。导航位于 AGENTS 之后。

SSE 生命周期与 TUI 相同：`POST /sessions`（client_type 用 `"webui"`）→ 立即 `PATCH` 切 `event` 模式 → `GET /events?session_id=` 开 `EventSource`；断线指数退避（1s→30s）后**从零重建会话**。顶栏常驻连接状态徽章（connected / reconnecting / down）。

## 视觉规范（cyberpunk）

设计 token 集中在 `styles/tokens.css`，全站唯一权威：

```css
:root {
  --bg: #0a0a12;  --bg-2: #12121f;
  --text: #e8e8f0;  --text-dim: #8a8aa0;
  --neon-cyan: #00f0ff;  --neon-magenta: #ff00e5;  --neon-yellow: #ffe600;
  --border-neon: 1px solid rgba(0, 240, 255, 0.6);
  --glow-cyan: 0 0 8px rgba(0,240,255,.8), 0 0 24px rgba(0,240,255,.4);
  --glow-magenta: 0 0 8px rgba(255,0,229,.6), 0 0 24px rgba(255,0,229,.3);
  --font-tech: 'Arial Black', 'Helvetica Neue', Impact, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', Menlo, monospace;
}
```

落地纪律：

- 主霓虹色**青**，辅**品红**（危险/拒绝/删除），**黄**只做点缀（未读、警示、断连）；霓虹色不超过 3 个。
- 背景深色 + 极淡网格线（`linear-gradient` 1px）+ 可选扫描线叠加层；面板/卡片一律 `bg-2` 底 + 1px 霓虹边框 + 四角 HUD 角标（`:before/:after` 小线段），圆角 ≤ 8px。
- 标题/主按钮 glow（`text-shadow`/`box-shadow` 多层叠加）；主按钮青底黑字，次按钮透明底 + 青描边。
- 科技标签等宽 10–12px uppercase、字距 0.2em；指标数字等宽 + 青 glow；EVENTS/LOGS 用终端面板样式（黑底、等宽青字、行首 `>`）。
- 禁用：浅色页面、大圆角、玻璃拟态、细标题字体。

## 托管与运行

- **开发**：`npm run dev`（vite dev server，`server.proxy` 把 REST/SSE 转发到 `127.0.0.1:8080`）。
- **生产**：`npm run build` 出 `dist/`；`RESTServer` 增加静态托管——`GET /ui` 重定向到 `/ui/`，`/ui/` 直出 `index.html`（aiohttp 3.13 的 `add_static` 不再为目录根服务 index），`/ui/*` 映射到 `apps/webui/dist/`；`dist` 不存在时不注册该路由（条件注册，与 admin/traces 路由同一取向：能力缺失以缺路由表达，不伪装）。单端口开箱即用：`tlon dream` 的启动 banner 会打印 WebUI 地址（未构建时打印构建提示，见 [app/server/serverapp.py](../../src/tlon/app/server/serverapp.py) 的 `_startup_banner`），浏览器打开即可。
- CORS 服务端已全开，dev 跨域无障碍；生产同源无 CORS 问题。

## 边界

- 不做认证/多用户——与本机信任边界一致（见 server-protocol.md「身份与安全边界」）。
- username 与 TUI 同规则：启动参数 / localStorage 持久，默认 `guest`。
- 不引入 react-router、状态管理库、UI 组件库——视图少且交互直给，手写最简。
