# 命令参考

> CLI、TUI、API 完整命令清单。

---

## CLI 命令

### 全局选项

```bash
tlon [选项] <命令>

选项:
  --version      显示版本
  --debug        启用调试日志
  -h, --help     显示帮助
```

### tlon server

启动服务端。

```bash
tlon server [选项]

选项:
  --host HOST         绑定地址 (默认: 127.0.0.1)
  --rest-port PORT    REST API 端口 (默认: 8080)
  --ws-port PORT      WebSocket 端口 (默认: 8888)
  --debug             调试模式

示例:
  tlon server
  tlon server --rest-port 9000 --ws-port 9001
  tlon server --host 0.0.0.0  # 允许远程连接
```

### tlon tui

启动 TUI 客户端。

```bash
tlon tui [选项]

选项:
  --host HOST         服务端地址 (默认: 127.0.0.1)
  --port PORT         WebSocket 端口 (默认: 8888)
  --rest-port PORT    REST API 端口 (默认: 8080)

示例:
  tlon tui
  tlon tui --host 192.168.1.100 --port 8888
```

### tlon wizard

运行配置向导。

```bash
tlon wizard

# 交互式配置:
# 1. 检查环境变量
# 2. 验证 API Key
# 3. 创建初始 Agent 配置
```

---

## 命令别名

Tlon 同时支持文学化命令名称（等效别名）：

| 工程名称 | 文学名称（别名） | 说明 |
|---------|-----------------|------|
| `tlon server` | `tlon dream` | 维持梦境（启动服务） |
| `tlon tui` | `tlon intrude` | 入侵现实（连接界面） |
| `tlon wizard` | `tlon mirror` | 镜中造物（配置向导） |

---

## TUI 命令

在 TUI 界面中输入。以 `/` 开头的是命令，其他是消息。

### 消息输入

```
> 你好                          # 发送消息给客服
> @陈代码 写个函数               # @提及特定 Agent
> @林知遥 @陈代码 讨论方案       # 同时@多个
```

### 模式切换

| 命令 | 说明 |
|-----|------|
| `/mode chat` | 切换到聊天模式 (默认) |
| `/mode event` | 切换到事件监控模式 |
| `/mode agent` | 切换到 Agent 管理模式 |

### Agent 管理

| 命令 | 说明 | 示例 |
|-----|------|------|
| `/wake <name>` | 唤醒 Agent | `/wake 陈代码` |
| `/sleep <name>` | 休眠 Agent | `/sleep 陈代码` |
| `/list` | 列出所有 Agent | - |
| `/status <name>` | 查看 Agent 状态 | `/status 陈代码` |

### 记忆操作

| 命令 | 说明 |
|-----|------|
| `/history [n]` | 显示最近 n 条记忆 (默认 10) |
| `/facts` | 显示已知事实 |
| `/summary` | 手动触发会话摘要 |
| `/forget <agent>` | 清除某 Agent 记忆 |

### 其他命令

| 命令 | 说明 |
|-----|------|
| `/help` | 显示帮助 |
| `/quit` 或 `/q` | 退出 TUI |
| `/clear` | 清屏 |

---

## TUI 快捷键

### 全局

| 快捷键 | 说明 |
|-------|------|
| `Ctrl+C` | 退出程序 |
| `Ctrl+L` | 清屏 |
| `Tab` | 切换焦点 |
| `↑/↓` | 浏览历史 |

### 输入框

| 快捷键 | 说明 |
|-------|------|
| `Enter` | 发送消息/执行命令 |
| `Ctrl+A` | 光标移到行首 |
| `Ctrl+E` | 光标移到行尾 |
| `Ctrl+U` | 清空输入 |

### 事件模式

| 快捷键 | 说明 |
|-------|------|
| `f` | 过滤事件类型 |
| `c` | 清除事件列表 |

---

## REST API

### 基础信息

```
Base URL: http://localhost:8080/api
Content-Type: application/json
```

### Agents

#### 列出所有 Agents

```bash
GET /api/agents

响应:
{
  "agents": [
    {
      "id": "dev-001",
      "name": "陈代码",
      "position": "developer",
      "status": "online",  // online, offline, sleeping
      "proactive": false
    }
  ]
}
```

#### 获取 Agent 详情

```bash
GET /api/agents/{id}

响应:
{
  "id": "dev-001",
  "name": "陈代码",
  "config": { ... },
  "state": {
    "status": "online",
    "last_active": "2024-03-09T10:23:45Z"
  },
  "memory_stats": {
    "working_entries": 5,
    "episodes": 12,
    "facts": 8
  }
}
```

#### 唤醒 Agent

```bash
POST /api/agents/{id}/wake

响应:
{ "status": "awakened", "container_id": "abc123" }
```

#### 休眠 Agent

```bash
POST /api/agents/{id}/sleep

响应:
{ "status": "sleeping" }
```

### Chatroom

#### 获取消息历史

```bash
GET /api/chatroom/messages?limit=50&before=timestamp

参数:
  limit:    返回消息数量 (默认 50)
  before:   获取此时间戳之前的消息

响应:
{
  "messages": [
    {
      "id": "msg-001",
      "timestamp": "2024-03-09T10:23:45Z",
      "sender": "user",
      "content": "优化API",
      "mentions": ["dev-001"]
    },
    {
      "id": "msg-002",
      "timestamp": "2024-03-09T10:23:46Z",
      "sender": "dev-001",
      "content": "收到，正在分析..."
    }
  ]
}
```

#### 发送消息

```bash
POST /api/chatroom/messages
Content-Type: application/json

请求体:
{
  "content": "@陈代码 优化这个函数",
  "mentions": ["dev-001"]
}

响应:
{ "id": "msg-003", "timestamp": "2024-03-09T10:24:00Z" }
```

### ~~Skills~~ ❌ 已移除

> Skill API 已于 2026-03-17 移除。原以下接口不再可用：
> - `GET /api/skills`
> - `POST /api/skills/{name}/activate`
> - `POST /api/skills/{name}/deactivate`

如需扩展 Agent 能力，请直接通过 `executeBashCommand` 工具执行命令。

响应:
{ "status": "activated" }
```

#### 停用 Skill

```bash
POST /api/skills/{name}/deactivate

响应:
{ "status": "deactivated" }
```

### 系统

#### 健康检查

```bash
GET /api/health

响应:
{ "status": "ok", "version": "0.1.0" }
```

#### 系统状态

```bash
GET /api/status

响应:
{
  "version": "0.1.0",
  "uptime": 3600,
  "agents": {
    "total": 5,
    "online": 2
  },
  "resources": {
    "memory": "1.2GB",
    "containers": 2
  }
}
```

---

## WebSocket API

### 连接

```javascript
// 客户端连接
const ws = new WebSocket('ws://localhost:8888');

ws.onopen = () => {
  // 订阅聊天室
  ws.send(JSON.stringify({
    type: 'subscribe',
    channel: 'chatroom'
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data);
};
```

### 消息格式

#### 客户端 → 服务端

```json
// 发送聊天消息
{
  "type": "chat",
  "content": "@陈代码 写代码",
  "mentions": ["dev-001"]
}

// 订阅频道
{
  "type": "subscribe",
  "channel": "chatroom"
}

// 执行命令
{
  "type": "command",
  "command": "/wake",
  "args": ["陈代码"]
}
```

#### 服务端 → 客户端

```json
// 新消息通知
{
  "type": "message",
  "data": {
    "id": "msg-004",
    "timestamp": "2024-03-09T10:25:00Z",
    "sender": "dev-001",
    "content": "代码已生成"
  }
}

// Agent 状态变更
{
  "type": "agent_status",
  "data": {
    "agent_id": "dev-001",
    "status": "thinking",
    "activity": "generating code"
  }
}

// 事件通知
{
  "type": "event",
  "data": {
    "timestamp": "2024-03-09T10:25:01Z",
    "type": "TOOL_CALL",
    "agent": "dev-001",
    "details": { "tool": "execute_bash", "args": [...] }
  }
}
```

---

## 错误码

### HTTP 状态码

| 码 | 说明 |
|---|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 (Agent 未启动) |

### 错误响应格式

```json
{
  "error": {
    "code": "AGENT_NOT_FOUND",
    "message": "Agent dev-999 does not exist"
  }
}

{
  "error": {
    "code": "CONTAINER_ERROR",
    "message": "Failed to start Docker container"
  }
}
```
