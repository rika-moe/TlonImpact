# Tlon 多界面架构设计方案

> **版本**: v2.0  
> **更新日期**: 2026-03-08  
> **主要变更**: TUI 采用极简流式控制台设计，废弃 Textual 全屏版本

---

## 1. 设计目标

支持三种用户交互方式：
1. **Terminal UI (TUI)** - 极简流式控制台，支持复制粘贴
2. **Web 页面** - 浏览器访问，支持富媒体（未来）
3. **聊天工具** - Telegram Bot 集成（未来）

同时提供命令：
- `minions server` - 启动核心服务进程
- `minions tui` - 启动 TUI 客户端（极简版本）
- `minions wizard` - 交互式配置工具

---

## 2. 架构概览

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           客户端层 (Clients)                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │     TUI      │  │    Web UI    │  │   Telegram   │  │   CLI (Legacy)  │  │
│  │   (极简流式  │  │   (Browser)  │  │    Bot       │  │  (Embedded)     │  │
│  │    控制台)   │  │              │  │              │  │                 │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘  │
│         │                  │                  │                   │         │
│         └──────────────────┴──────────────────┴───────────────────┘         │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     Client SDK (Universal API)                       │   │
│  │  - 连接管理 (WebSocket/REST)                                         │   │
│  │  - 消息收发                                                          │   │
│  │  - 事件订阅                                                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          │ IPC / Network
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           服务端层 (Server)                                  │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      API Gateway (FastAPI)                           │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │ REST API     │  │  WebSocket   │  │  Auth        │              │   │
│  │  │  /api/v1/*   │  │  /ws/v1/*    │  │  (Token)     │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      组织层 (Organization)                           │   │
│  │                    （原「核心层 Core」，管理协作基础设施）             │   │
│  │                                                                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │  EventBus    │  │  ChatRoom    │  │AgentRegistry │              │   │
│  │  │  (Internal)  │  │  (State)     │  │ (Lifecycle)  │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  │                                                                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │   Agent 1    │  │   Agent 2    │  │   Agent N    │              │   │
│  │  │  (Docker)    │  │  (Docker)    │  │  (Docker)    │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    🔒 Sandbox 层 (Docker)                            │   │
│  │  - 每个 Agent 独立的隔离执行环境                                        │   │
│  │  - executeBashCommand 在此层执行                                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Session Manager                                   │   │
│  │  - 用户会话管理                                                       │   │
│  │  - 多客户端同步                                                       │   │
│  │  - 在线状态追踪                                                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           进程模型                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        minions server                               │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │ HTTP Server  │  │  WebSocket   │  │  Core Engine │              │   │
│  │  │   :8080      │  │   :8080/ws   │  │  (EventBus)  │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  │                                                                     │   │
│  │  - 长期运行的守护进程                                                │   │
│  │  - 管理所有 Agent 生命周期                                           │   │
│  │  - 维护 ChatRoom 状态                                                │   │
│  │  - 对外暴露 API 接口                                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ▲                                         │
│                    ┌───────────────┼───────────────┐                        │
│                    │               │               │                        │
│                    ▼               ▼               ▼                        │
│  ┌──────────────────────┐ ┌──────────────┐ ┌──────────────────────┐       │
│  │    minions tui       │ │  Web Browser │ │   Telegram Bot       │       │
│  │  ┌────────────────┐  │ │              │ │  ┌────────────────┐  │       │
│  │  │ 极简流式控制台 │  │ │  React/Vue   │ │  │ Bot Polling    │  │       │
│  │  │ prompt-toolkit │  │ │  Frontend    │ │  │ Webhook Handler│  │       │
│  │  │ + patch_stdout │  │ │              │ │  └────────────────┘  │       │
│  │  └────────────────┘  │ │              │ │                      │       │
│  │  - WebSocket Client  │ │ - HTTP Client│ │  - HTTP Client       │       │
│  │  - 三模式切换        │ │              │ │                      │       │
│  │  - 可复制粘贴        │ │              │ │                      │       │
│  └──────────────────────┘ └──────────────┘ └──────────────────────┘       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     minions wizard                                  │   │
│  │  - 独立工具进程                                                      │   │
│  │  - 修改配置、初始化环境                                              │   │
│  │  - 不依赖 server 运行                                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件设计

### 3.1 Server 进程 (minions server)

**职责**：
- 作为核心引擎长期运行
- 管理所有 Agent 的生命周期
- 维护 ChatRoom 状态和消息历史
- 对外暴露 REST API 和 WebSocket 接口

**启动流程**：
```python
# tlon/server/serverapp.py
class ServerApp:
    async def initialize(self):
        # 1. 启动 EventBus（内部消息总线）
        self.event_bus = EventBus()
        await self.event_bus.start()
        
        # 2. 创建 ChatRoom
        self.chatroom = ChatRoom(...)
        await self.chatroom.initialize()
        
        # 3. 创建 AgentRegistry
        self.agent_registry = AgentRegistry(...)
        await self.agent_registry.initialize()
        
        # 4. 创建 SessionManager（管理多客户端）
        self.session_manager = SessionManager(...)
        
        # 5. 启动 API Server
        self.api_server = APIServer(...)
        await self.api_server.start()
```

**API 设计**：
```yaml
# REST API
POST   /api/v1/auth/token        # 获取访问令牌
GET    /api/v1/chatroom           # 获取聊天室信息
GET    /api/v1/chatroom/history   # 获取历史消息
POST   /api/v1/chatroom/message   # 发送消息
GET    /api/v1/agents             # 获取 Agent 列表
GET    /api/v1/agents/{id}        # 获取 Agent 详情
POST   /api/v1/agents/{id}/wake   # 唤醒 Agent

# WebSocket API
WS     /ws/v1/chatroom            # 实时消息流
WS     /ws/v1/events              # 系统事件流
```

### 3.2 TUI Client (minions tui)

**技术选型**：
- **prompt-toolkit**: 异步输入 + `patch_stdout` 保持输入行
- **Rich**: 富文本渲染、表格输出
- **aiohttp**: WebSocket 客户端

**界面形态**：
```
🐙 Tlon TUI Client (极简模式)
Connecting to ws://localhost:8888/ws...
✅ Connected to ws://localhost:8888/ws
📡 5 agents available

输入 /help 查看帮助，/quit 退出

[10:23:12] 🤖 客服: 欢迎使用 Tlon！
[10:23:15] 👤 你: @客服 帮我看一下问题
[10:23:18] 🤖 客服: 收到，我来初步诊断一下...

[chat] > @客服 帮我看一下...
         ↑
         输入行固定在底部
```

**核心设计**：
```python
# tlon/cli/tui_simple.py

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

class SimpleTUIClient:
    """极简 TUI 客户端"""
    
    def __init__(self, ...):
        self.current_mode = "chat"  # chat | event | agent
        self.session = PromptSession()
        
    async def run(self):
        with patch_stdout():  # 保持输入行在底部
            asyncio.create_task(self._receive_loop())
            
            while True:
                prompt = f"[{self.current_mode}] > "
                user_input = await self.session.prompt_async(prompt)
                await self._handle_input(user_input)
                
    async def _handle_input(self, text: str):
        """处理用户输入"""
        if text.startswith('/'):
            await self._handle_command(text)
        elif self.current_mode == "chat":
            await self._send_message(text)
```

**三模式设计**：

| 模式 | 特点 | 触发方式 |
|------|------|----------|
| **Chat** | 消息流输出，支持 @提及 | `/mode chat` |
| **Event** | 系统事件监控，支持过滤/暂停 | `/mode event` |
| **Agent** | Agent 管理，命令触发表格 | `/mode agent` |

### 3.3 Web Client (未来)

**技术栈**：
- 后端：FastAPI（与 server 一起或独立）
- 前端：React/Vue + WebSocket
- 样式：Tailwind CSS

**界面设计**：类似 Discord/Slack 的聊天界面

### 3.4 Telegram Client (未来)

作为独立进程运行，通过 HTTP API 与 server 通信。

### 3.5 Wizard (minions wizard)

**职责**：
- 初始化项目（创建 `.env`, `config/agents.yaml`）
- 验证配置
- 管理 Agent 配置
- 检查 Docker 环境

**交互流程**：
```
$ minions wizard

🧙‍♂️  Tlon 配置向导

1. 环境检查
   ✓ Python 3.11+
   ✓ Docker 运行中
   ✓ 网络连接正常

2. API 配置
   请输入 OpenAI API Key: sk-****************
   请选择模型: [gpt-4] gpt-3.5-turbo
   
3. Agent 配置
   要创建哪些 Agent?
   [x] 客服 (常驻)
   [x] 项目经理
   [x] 开发-A
   [ ] 开发-B
   [x] 运维
   
4. 完成
   配置已保存到 .env 和 config/agents.yaml
   运行 `minions server` 启动服务
```

---

## 4. 通信协议

### 4.1 客户端-服务端协议

**WebSocket 消息格式**：
```typescript
// 客户端 → 服务端
interface ClientMessage {
    type: "message" | "command" | "ping";
    payload: any;
    timestamp: string;
}

// 服务端 → 客户端
interface ServerMessage {
    type: "message" | "event" | "status" | "pong";
    payload: any;
    timestamp: string;
}

// 示例：发送消息
{
    "type": "message",
    "payload": {
        "content": "@开发-A 帮我看一下代码",
        "mentions": ["dev-001"]
    },
    "timestamp": "2026-03-07T21:30:35Z"
}
```

**事件类型**：
```typescript
type EventType = 
    | "agent.online"      // Agent 上线
    | "agent.offline"     // Agent 离线
    | "agent.thinking"    // Agent 思考中
    | "agent.typing"      // Agent 正在输入
    | "message.new"       // 新消息
    | "message.mention"   // 被@提及
    | "system.error"      // 系统错误
    | "session.sync";     // 会话同步
```

### 4.2 进程间通信 (IPC)

**本地模式**（客户端与 server 在同一机器）：
- Unix Socket: `/tmp/minions.sock`
- 或 TCP: `localhost:8080`

**远程模式**：
- WebSocket over TLS (wss://)
- 支持 Token 认证

---

## 5. 代码结构

```
src/tlon/
│
├── __init__.py                     # 包入口
├── __main__.py                     # 模块入口
│
├── app/                            # 【应用层】CLI、Server、Clients、Wizard
│   ├── cli/                        #   命令行入口
│   │   ├── __init__.py
│   │   └── main.py                #     命令分发
│   ├── clients/                    #   客户端实现
│   │   ├── __init__.py
│   │   ├── sdk.py                 #     SDK 客户端 (TlonClient)
│   │   ├── tui/                   #     TUI 客户端
│   │   │   ├── __init__.py
│   │   │   └── tui_simple.py      #       极简 TUI 实现 ⭐ 主推
│   │   ├── webui/                 #     Web 前端 (预留)
│   │   └── imui/                  #     IM 客户端 (预留，如 Telegram)
│   ├── server/                     #   服务端
│   │   ├── __init__.py
│   │   ├── app.py                 #     Server 主类 (ServerApp)
│   │   ├── auth.py                #     认证管理
│   │   ├── session.py             #     会话管理
│   │   └── api/                   #     API 层
│   │       ├── __init__.py
│   │       ├── rest.py            #       REST endpoints
│   │       └── websocket.py       #       WebSocket handlers
│   └── wizard/                     #   配置向导 (Mirror)
│       ├── __init__.py
│       ├── core.py                #     向导核心
│       ├── validators.py          #     验证器
│       ├── templates.py           #     模板
│       └── menus/                 #     菜单
│
├── base/                           # 【基础层】配置、事件、日志
│   ├── __init__.py
│   ├── logger.py                  #   日志配置
│   ├── settings.py                #   全局设置
│   ├── config/                     #   配置管理
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── loader.py
│   │   ├── models.py
│   │   ├── prompts.py
│   │   ├── saver.py
│   │   └── validator.py
│   └── events/                     #   事件系统
│       ├── __init__.py
│       ├── event.py               #     Event 类 (原 protocol/)
│       └── event_bus.py           #     EventBus (原 messaging/)
│
├── organization/                   # 【组织层】多 Agent 协作基础设施
│   ├── __init__.py
│   ├── messaging/                  #   消息系统
│   │   ├── __init__.py
│   │   ├── congress.py            #     Congress (代表大会)
│   │   └── message.py             #     Message 类 (原 protocol/)
│   └── positions/                  #   岗位系统
│       ├── __init__.py
│       ├── models.py
│       ├── registry.py
│       └── prompt_renderer.py
│
├── agents/                         # 【Agent 层】智能体核心
│   ├── __init__.py
│   ├── agent.py                   #   Agent 核心类
│   ├── agent_registry.py          #   Agent 注册表
│   ├── llm_client.py              #   LLM 客户端
│   ├── prompt_builder.py          #   系统提示构建
│   └── tool_executor.py           #   工具执行器
│
├── sandbox/                        # 【Sandbox 层】Docker 隔离
│   ├── __init__.py
│   └── docker_runtime.py          #   Docker 运行时
│
└── subsystem/                      # 【子系统层】Memory、Skills、Tools
    ├── __init__.py
    ├── memory/                     #   记忆系统 (Shakespeare)
    │   ├── __init__.py
    │   ├── base.py
    │   ├── manager.py
    │   ├── models.py
    │   ├── working_store.py
    │   ├── episodic_store.py
    │   └── summarizer.py
    ├── skills/                     #   技能系统 (Library of Babel)
    │   ├── __init__.py
    │   ├── manager.py
    │   ├── models.py
    │   └── validator.py
    └── tools/                      #   工具层
        ├── __init__.py
        ├── base.py                #     工具基类
        ├── complete_dialog/        #     对话完成工具
        ├── memory/                 #     记忆工具
        ├── shell/                  #     Shell 工具
        ├── skills/                 #     技能工具
        └── tasks/                  #     任务工具 + 任务管理
```

---

## 6. 命令设计

### 6.1 minions server

```bash
# 启动 server（前台运行）
tlon server

# 后台运行
tlon server --daemon

# 指定端口
tlon server --port 8080 --host 0.0.0.0

# 指定配置
tlon server --config ./my-config.yaml

# 查看状态
tlon server status

# 停止后台服务
tlon server stop
```

### 6.2 minions tui

```bash
# 连接到本地 server（极简版本）
tlon tui

# 指定服务器
tlon tui -h 192.168.1.100 -w 8888 -r 8080

# 帮助
tlon tui --help
```

### 6.3 minions wizard

```bash
# 启动交互式向导
tlon wizard

# 检查环境
tlon wizard check

# 重置配置
tlon wizard reset
```

---

## 7. TUI 三模式命令参考

### 7.1 全局命令（所有模式）

| 命令 | 简写 | 说明 |
|------|------|------|
| `/help` | `/h` | 显示帮助 |
| `/quit` | `/q`, `/exit` | 退出 TUI |
| `/clear` | `/cls` | 清屏 |
| `/mode <chat\|event\|agent>` | `/m` | 切换模式 |
| `/connect` | `/c` | 重新连接 |
| `/status` | `/s` | 显示连接状态 |
| `/agents` | `/a` | 列出 Agent |

### 7.2 Chat 模式

| 命令 | 说明 |
|------|------|
| `/history [n]` | 显示最近 n 条历史 |
| `/who` | 显示在线成员 |

### 7.3 Event 模式

| 命令 | 说明 |
|------|------|
| `/filter [条件]` | 设置过滤器 |
| `/filter clear` | 清除过滤器 |
| `/pause` | 暂停滚动 |
| `/resume` | 恢复滚动 |
| `/tail [n]` | 显示最近 n 条 |

### 7.4 Agent 模式

| 命令 | 简写 | 说明 |
|------|------|------|
| `/list` | `/ls` | 打印状态表格 |
| `/wake <id>` | - | 唤醒 Agent |
| `/sleep <id>` | - | 休眠 Agent |
| `/restart <id>` | - | 重启 Agent |
| `/logs <id>` | - | 查看日志 |
| `/top` | - | 资源监控 |

---

## 8. 启动流程

### 8.1 Server 启动

```bash
$ tlon server

🚀  Tlon Server v0.1.0

[2026-03-07 21:30:35] INFO  Loading configuration...
[2026-03-07 21:30:35] INFO  ✓ Config loaded from config/agents.yaml
[2026-03-07 21:30:35] INFO  Initializing core services...
[2026-03-07 21:30:35] INFO  ✓ EventBus started
[2026-03-07 21:30:35] INFO  ✓ ChatRoom '主聊天室' initialized
[2026-03-07 21:30:35] INFO  Loading agents...
[2026-03-07 21:30:35] INFO  ✓ Registered 5 agents (1 resident, 4 standby)
[2026-03-07 21:30:38] INFO  ✓ Agent '客服' (cs-001) started (resident)
[2026-03-07 21:30:38] INFO  Starting API server...
[2026-03-07 21:30:38] INFO  ✓ REST API: http://localhost:8080/api/v1
[2026-03-07 21:30:38] INFO  ✓ WebSocket: ws://localhost:8080/ws/v1
[2026-03-07 21:30:38] INFO  Server ready!
```

### 8.2 TUI 启动

```bash
$ tlon tui

🐙 Tlon TUI Client (极简模式)
Connecting to ws://localhost:8888/ws...
✅ Connected to ws://localhost:8888/ws
📡 5 agents available

输入 /help 查看帮助，/quit 退出

[chat] >
```

---

## 9. 状态同步机制

### 9.1 多客户端同步

当多个客户端（TUI + Web）同时连接时：

1. **消息同步**：所有客户端实时接收新消息
2. **状态同步**：Agent 在线/离线状态实时推送
3. **输入指示**：显示"xxx 正在输入..."

### 9.2 离线消息

客户端断开连接后：
- 服务端缓存最近消息
- 客户端重连后自动同步错过的消息

---

## 10. 安全考虑

1. **认证**：Token-based 认证（JWT）
2. **传输**：支持 TLS/WSS
3. **CORS**：Web 客户端跨域配置
4. **限流**：API 速率限制

---

## 11. 实现优先级（更新版）

### Phase 1: Server 架构 ✅
- [x] 重构 `main.py` 为 `cli/main.py`
- [x] 实现 `server/app.py`
- [x] 实现 REST API
- [x] 实现 WebSocket 支持
- [x] 实现 SessionManager

### Phase 2: TUI Client ✅（已更新）
- [x] 实现 `clients/sdk.py`
- [x] 实现极简 TUI (`tui_simple.py`)
- [x] 集成 prompt-toolkit + patch_stdout
- [x] 实现三模式切换
- [ ] ~~集成 Textual 框架~~ (已废弃)

### Phase 3: Wizard 🔄
- [ ] 实现配置验证
- [ ] 实现交互式配置
- [ ] 实现环境检查

### Phase 4: Web Client (未来)
- [ ] 前端框架搭建
- [ ] 实现 WebSocket 客户端
- [ ] 实现聊天界面

### Phase 5: Telegram (未来)
- [ ] 实现 Telegram Bot
- [ ] Webhook/Long polling 支持

---

## 12. 废弃与迁移

### 12.1 废弃组件

| 组件 | 状态 | 说明 |
|------|------|------|
| `tui_textual.py` | 废弃 | 全屏 TUI 不支持复制粘贴 |
| Textual 依赖 | 可选 | 不再默认安装 |

### 12.2 迁移指南

```bash
# 废弃命令（不再可用）
$ tlon tui --textual

# 新命令（统一入口）
$ tlon tui                    # 启动极简版本
$ tlon tui -h <host>          # 指定服务器
```

---

## 附录：技术选型对比

| 特性 | 旧方案 (Textual) | 新方案 (极简) |
|------|-----------------|--------------|
| 界面类型 | 全屏 TUI | 流式控制台 |
| 复制粘贴 | 受限 | ✅ 完全支持 |
| 历史回看 | 组件内滚动 | 终端原生滚动 |
| Agent 刷新 | 自动实时 | 命令触发 |
| 代码复杂度 | 高 (~1100行) | 低 (~500行) |
| 依赖 | Textual + Rich | prompt-toolkit + Rich |
| 维护成本 | 高 | 低 |
