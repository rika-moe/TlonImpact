# 命令参考

> CLI、Operator 控制台、REST、WebSocket、SDK 完整清单。

---

> 所有命令需在项目目录下通过 `uv run tlon` 执行。下文以 `tlon` 指代，实际输入时加 `uv run` 前缀。

---

## CLI 命令

### 全局选项

```bash
uv run tlon [选项] <命令>

选项:
  --version      显示版本
  --debug        启用调试日志
  -h, --help     显示帮助
```

### tlon dream

启动服务端（REST + WebSocket）。

```bash
uv run tlon dream [选项]

选项:
  --host HOST         绑定地址 (默认: 127.0.0.1)
  --rest-port PORT    REST API 端口 (默认: 8080)
  --ws-port PORT      WebSocket 端口 (默认: 8888)
  --lucid, -l         清醒梦模式（调试日志）

示例:
  uv run tlon dream
  uv run tlon dream --rest-port 9000 --ws-port 9001
  uv run tlon dream --host 0.0.0.0  # 允许远程连接
```

### tlon ops

打开 Operator 控制台——人类的邮件主界面兼运维监控台（TypeScript/Ink）。

```bash
uv run tlon ops [选项]

选项:
  --host HOST           服务端地址 (默认: 127.0.0.1)
  --username, -u NAME   邮件身份 (默认: guest，可用环境变量 TLON_USERNAME)
  --port, -p PORT       WebSocket 端口 (默认: 8888)
  --rest-port, -r PORT  REST API 端口 (默认: 8080)
  --lucid, -l           清醒梦模式（调试日志）

示例:
  uv run tlon ops --username alice
  uv run tlon ops --host 192.168.1.100 --port 8888
```

`--username` 决定你的邮件地址 `<username>@tlon.internal`；同名会话共享同一身份和邮箱。

### tlon intrude / tlon tui

`ops` 的旧别名，保留仅为向后兼容。功能与 `tlon ops` 完全一致，参数相同。新文档和脚本请一律使用 `ops`。

### tlon mirror

运行配置向导。

```bash
uv run tlon mirror

# 交互式配置:
# 1. 检查环境变量
# 2. 验证 API Key
# 3. 创建初始 Agent 配置
```

---

## 命令别名

| 文学名称 | 工程别名 | 说明 |
|---------|---------|------|
| `uv run tlon dream` | `uv run tlon server` | 维持梦境（启动服务） |
| `uv run tlon ops` | `uv run tlon intrude` / `uv run tlon tui` | 入侵现实（Operator 控制台） |
| `uv run tlon mirror` | `uv run tlon wizard` | 镜中造物（配置向导） |

---

## Operator 控制台（TUI）

`tlon ops` 启动后是一个五视图控制台。所有交互都是**写信/读信 + 监控**，没有 `/` 命令。

### 全局按键

| 按键 | 操作 |
|------|------|
| `1` – `5` | 切换到 mail / agents / events / traces / config 视图 |
| `tab` / `→` | 下一个视图 |
| `←` | 上一个视图 |
| `q` / `Ctrl+C` | 退出 |

写信或回复表单激活时，全局按键挂起，表单独占键盘。

### mail 视图（默认）

左栏线程列表（按 `thread_id` 聚合，最新在前），右栏选中线程的会话流。

| 按键 | 操作 |
|------|------|
| `↑` / `↓` | 选择线程 |
| `c` | 写信：To / Cc / Subject / Content 四字段，回车逐字段，Content 上回车提交，`esc` 取消 |
| `r` | 回复选中线程：自动填收件人、`Re:` 主题，继承线程（`in_reply_to` / `thread_id`） |

To/Cc 支持逗号分隔多个收件人；每项可以是完整地址（`lin-zhiyao@tlon.internal`）、agent id（`lin-zhiyao`）或显示名（`林知遥`）。邮件必须至少有一个可解析收件人，否则服务端拒绝。特殊地址 `all@tlon.internal` 展开为全部 agent + 在线人类；错误地址会收到 `postmaster@tlon.internal` 退信。

### agents / events / traces / config 视图

| 视图 | 按键 | 用途 |
|------|------|------|
| agents | `↑↓` 选择 · `enter` 详情 · `r` 刷新 | agent 列表与状态，单个 agent 详情 |
| events | `↑↓` 滚动 · `p` 暂停 · `c` 清空 | 实时事件流（最近 500 条），选中事件查看 JSON |
| traces | `↑↓` 选择 · `enter` 详情 · `r` 刷新 | 调用链列表，完整 trace 与 span 树 |
| config | `r` 刷新 | 只读查看服务端与 agent 配置 |

控制台认证后自动切到 `event` 会话模式，因此能看到**全部**事件（包括他人之间的邮件投递）；mail 视图在客户端侧只呈现与你自己有关的邮件。模式语义见下文 WebSocket API。

---

## REST API

### 基础信息

```
Base URL: http://localhost:8080
Content-Type: application/json
CORS: Access-Control-Allow-Origin: *
```

注意：端点挂在根路径下（`/agents`），没有 `/api` 前缀。

### 系统

#### 健康检查

```bash
GET /health

响应:
{
  "status": "healthy",
  "running": true,
  "timestamp": "2026-07-30T10:23:45.123456",
  "version": "0.2.0"
}
```

#### 服务端配置

```bash
GET /config

响应:
{
  "server": { "host": "127.0.0.1", "port": 8888 },
  "agents": { "count": 6 },
  "sessions": { "active": 1 }
}
```

### 邮件

#### 拉取人类收件箱

```bash
GET /inbox?username=alice&limit=50

参数:
  username: 人类用户名 (默认 guest)；返回 "human:<username>" 的邮箱
  limit:    最大返回数 (默认 50，上限 200)

响应:
{
  "mails": [
    {
      "id": "3f2a...",
      "from_address": "gu-yanxi@tlon.internal",
      "to": ["alice@tlon.internal"],
      "cc": [],
      "subject": "Re: 你好",
      "content": "收到，顾言蹊在线...",
      "in_reply_to": "9c1b...",
      "thread_id": "9c1b...",
      "forward_of": null,
      "timestamp": "2026-07-30T10:23:46.789012+00:00"
    }
  ],
  "count": 1
}
```

返回该成员 inbox + sent 合并、按时间序的邮件。username 为空或含路径分隔符返回 400。

### Agents

#### 列出所有 Agents

```bash
GET /agents

响应:
{
  "agents": [
    { "id": "lin-zhiyao", "name": "林知遥", "state": "running", ... }
  ],
  "count": 6
}
```

#### 获取 Agent 详情

```bash
GET /agents/{id}

响应:
{
  "id": "lin-zhiyao",
  "name": "林知遥",
  "state": "running",
  "config": {
    "profile": "开发工程师，技术部。负责代码编写..."
  }
}
```

agent 不存在返回 404 `{"error": "Agent 'xxx' not found"}`。

> 唤醒/休眠端点已移除：所有 agent 随服务端启动，空闲自动沉睡，收到邮件自动唤醒，无需手动管理。

### 参与者与会话

```bash
GET /participants

响应:
{
  "participants": [
    { "id": "lin-zhiyao", "name": "林知遥", "type": "agent", "metadata": {...} },
    { "id": "human:alice", "name": "alice", "type": "human", "metadata": {...} }
  ],
  "count": 7
}
```

```bash
GET /sessions

响应:
{
  "sessions": [
    {
      "id": "sess-uuid",
      "client_type": "tui",
      "connected_at": "2026-07-30T10:20:00",
      "mode": "event",
      "filters": {...}
    }
  ],
  "count": 1
}
```

### 日志与 Trace

#### 全局日志

```bash
GET /logs?agent_id=lin-zhiyao&level=warning&trace_id=abc&limit=100&before=<ISO时间>

参数（均可选，组合为 AND）:
  agent_id / trace_id / level (debug|info|warning|error|critical，最低级别) / before / limit (默认 100，上限 1000)

响应: { "logs": [ ...结构化日志条目... ], "count": N }
```

读取当天和昨天的 `shared/logs/server-{date}.jsonl`。

#### 单个 Agent 日志

```bash
GET /agents/{id}/logs?limit=100&before=<ISO时间>

响应: { "agent_id": "lin-zhiyao", "logs": [...], "count": N }
```

#### Trace

```bash
GET /traces?limit=100&source=mail_delivery   # 列表（limit 上限 1000）
GET /traces/{trace_id}                        # 完整 trace + span 树

响应 (详情):
{ "trace": {...}, "span_tree": {...} }
```

### 管理

#### 当前 Agent 配置（热加载）

```bash
GET /admin/config/agents

响应:
{
  "status": "success",
  "agents": [ { "id": "lin-zhiyao", "name": "林知遥", "identity": "你是林知遥..." } ],
  "count": 6
}
```

仅在服务端装配了 config manager 时可用，否则 503。

### 已移除的端点

以下端点已随邮件模型切换（R2/PR-C）和 Skill 系统移除而删除：

- `GET /history`、`GET|POST /api/chatroom/*` — 邮件历史走 `GET /inbox`，发信走 WebSocket `message` 帧或 SDK
- `POST /agents/{id}/wake|sleep` — 生命周期全自动
- `GET|POST /api/skills/*` — Skill 系统已于 2026-03-17 移除
- `GET /api/status` — 由 `/health` + `/config` 取代

---

## WebSocket API

### 连接与认证

端点 `ws://localhost:8888/ws`。连接后**第一帧必须是 auth**（10 秒超时），认证失败连接以 4001 关闭。

```json
// Client → Server: auth
{
  "type": "auth",
  "client_type": "tui",
  "username": "alice",
  "protocol_version": "1.0.0",
  "supported_versions": ["1.0.0"],
  "capabilities": ["trace", "filter"],
  "filters": { "exclude_event_types": ["agent.llm_request"] },
  "metadata": { "client_version": "0.2.0" }
}

// Server → Client: 成功
{
  "type": "auth_success",
  "session_id": "sess-uuid",
  "username": "alice",
  "protocol_version": "1.0.0",
  "server_version": "0.2.0",
  "server_time": "2026-07-30T10:20:00.000000",
  "capabilities": ["trace", "filter"],
  "timestamp": "2026-07-30T10:20:00.000000"
}

// Server → Client: 版本不兼容
{
  "type": "auth_failed",
  "error_code": "VERSION_MISMATCH",
  "message": "Protocol version 0.9.0 is no longer supported...",
  "supported_versions": ["1.0.0"]
}
```

- `username` 可选，缺省 `guest`；它决定本会话以哪个 `human:<username>` 身份发信、收信。
- `protocol_version` 缺省按 1.0.0 处理；客户端版本更高时降级到服务端版本。
- `filters` 声明初始事件过滤器（可选），之后可用 `filter` 帧整体替换。

### Client → Server 帧

#### 发送邮件

```json
{
  "type": "message",
  "content": "请帮我审查这段代码",
  "to": ["lin-zhiyao@tlon.internal"],
  "cc": ["shen-qingyan"],
  "subject": "代码审查请求",
  "in_reply_to": "上游邮件id（回复时）",
  "thread_id": "线程根邮件id（回复时）"
}
```

- `content` 必填（空 → 错误帧 `Message content is empty`）。
- `to` / `cc` 可选但均为 list；每项可以是完整地址、agent id 或显示名，服务端解析去重。**零可解析收件人 → 错误帧，不发布**（"没有可送达的收件人：请在 to 中指定至少一个收件人"）。
- `subject` / `in_reply_to` / `thread_id` 可选字符串；不带 `thread_id` 的新邮件自成线程根。
- 无法解析的收件人地址触发 `postmaster@tlon.internal` 退信到发件人邮箱。
- 未知字段静默忽略（例如旧客户端残留的 `mentions` 别名——已于 PR-C 移除）。

#### 切换模式

```json
{ "type": "mode", "mode": "chat" }

// 响应: { "type": "mode_changed", "mode": "chat" }
```

`mode` 取 `chat` / `event` / `agent`，语义见下表。切换后服务端按新模式补发初始数据（chat → `participants` 帧，agent → `agents` 帧）。

#### 更新过滤器

```json
{ "type": "filter", "filters": { "trace_id": "trace-abc-123" } }

// 响应: { "type": "filters_updated", "filters": {...} }
```

#### 心跳

```json
{ "type": "ping" }
// 响应: { "type": "pong" }
```

### Server → Client 帧

#### 事件

```json
{
  "type": "event",
  "event_type": "mail.delivered",
  "payload": {
    "mail": {
      "id": "3f2a...",
      "from_address": "gu-yanxi@tlon.internal",
      "to": ["alice@tlon.internal"],
      "cc": [],
      "subject": "Re: 你好",
      "content": "...",
      "in_reply_to": "9c1b...",
      "thread_id": "9c1b...",
      "forward_of": null,
      "timestamp": "2026-07-30T10:23:46.789012+00:00"
    },
    "recipient": "human:alice"
  },
  "source": "mail_delivery",
  "timestamp": "2026-07-30T10:23:46.790000",
  "trace_id": "trace-abc-456",
  "span_id": "span-001",
  "parent_span_id": "span-000"
}
```

- 每个收件人一份 `mail.delivered` 事件（`recipient` 标识落在谁的邮箱）；新邮件投递前另有 `mail.new`（payload 为 Mail 本体）。
- trace 字段（`trace_id` / `span_id` / `parent_span_id`）仅在事件携带时出现。
- 其余事件类型（`agent.started/stopped/error/state_changed`、`agent.thinking_*`、`agent.llm_*`、`tool.execute_*`、`system.*`、`session.*`）见 [协议设计](../../design/client-server-protocol.md)。

#### 初始数据 / 状态 / 错误

```json
{ "type": "participants", "participants": [ {"id","name","type"} ] }
{ "type": "agents", "agents": [ ... ] }
{ "type": "mode_changed", "mode": "event" }
{ "type": "filters_updated", "filters": {...} }
{ "type": "error", "message": "Field 'to' must be a list" }
{ "type": "pong" }
```

### 模式语义与事件过滤

过滤**只在服务端执行**（客户端只是声明）。管线依次：

0. **人类邮件单轨投递**：`mail.delivered` 在 chat 模式下不经 NotificationService 推送——人类收信走 HumanAdapter（按 `recipient` == 自己的人类身份过滤后向本会话扇出）；event / agent 模式不受影响——运维控制台因此能看到全部邮件流量。
1. **模式语义**：
   - `chat`：`mail.*`、`agent.response/started/stopped/error`、`system.*`
   - `agent`：仅 `agent.*`
   - `event`：全部事件
2. **会话过滤器**（均可选，AND 关系）：
   - `event_types`：包含列表（`prefix.*` 通配）
   - `exclude_event_types`：排除列表（`prefix.*` 通配）
   - `trace_id`：仅推送该 trace 的事件
   - `agent`：按事件来源匹配（`*` 通配）

---

## Python SDK

`TlonClient` 封装了连接、认证、协议协商与事件分发：

```python
import asyncio
from tlon.app.clients.sdk.client import TlonClient

async def main() -> None:
    async with TlonClient("ws://localhost:8888/ws") as client:
        await client.authenticate(username="alice")

        # 发信：to 为必填列表，接受地址 / agent id / 显示名
        await client.send_message(
            "帮我审查这段代码",
            to=["lin-zhiyao@tlon.internal"],
            cc=["shen-qingyan"],
            subject="代码审查请求",
        )

        # 收事件（服务端已按模式和过滤器筛过）
        @client.on_event
        def handle(event: dict) -> None:
            if event["event_type"] == "mail.delivered":
                mail = event["payload"]["mail"]
                print(f"{mail['from_address']}: {mail['content']}")

        await asyncio.sleep(60)

asyncio.run(main())
```

常用方法：

| 方法 | 说明 |
|------|------|
| `connect()` / `disconnect()` | 连接管理（支持自动重连，重连后自动重新认证） |
| `authenticate(username=...)` | 认证，`username` 缺省服务端按 `guest` 处理 |
| `send_message(content, to, cc=None, subject=None, reply_to=None)` | 发送邮件（R2 协议） |
| `set_mode("chat"\|"event"\|"agent")` | 切换模式（同时重同步过滤器） |
| `set_filters({...})` / `set_trace_filter(trace_id)` | 更新会话过滤器 |
| `on_event` / `on_message` / `on_error` / `on_state_change` / `on_reconnect` | 注册回调 |
| `get_agents()` / `get_agent(id)` / `get_traces()` / `get_trace(id)` / `get_agent_logs(id)` | REST 便捷封装 |

---

## 错误码

### HTTP 状态码

| 码 | 说明 |
|---|------|
| 200 | 成功 |
| 400 | 请求参数错误（如非法 username、非法 JSON） |
| 404 | 资源不存在（agent / trace） |
| 503 | 存储或组件不可用（mail storage / trace store / config manager） |

### 错误响应格式

REST 错误为扁平结构：

```json
{ "error": "Agent 'dev-999' not found" }
```

WebSocket 错误为 `error` 帧：

```json
{ "type": "error", "message": "没有可送达的收件人：请在 to 中指定至少一个收件人" }
```
