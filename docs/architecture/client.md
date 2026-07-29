# client 进程：Operator TUI

> 命令：`tlon ops`（`OperatorApp`，TypeScript/Ink，apps/operator）
> 通过 WebSocket 连接到 dream 进程。
>
> ❌ **Python Congress TUI（`tlon chat`）已退役（2026-07，PR-C）**——人类的
> 邮件主界面是 Operator 的 mail 视图（WS `to`/`cc`/`subject` + REST `/inbox`）。
> 下方 CongressApp 章节仅作历史参考；原 Python OperatorApp 也已迁移到
> apps/operator（TS/Ink），对应章节同为历史记录。

---

## CongressApp（`tlon chat`，已退役）

**架构**: 单栏聊天界面

```
CongressApp (Textual App)
├── Header（系统标题栏 + 时钟）
├── #header-bar
│   ├── 💬 Congress（标题）
│   ├── agent-count
│   └── connection-status
├── ChatView（VerticalScroll）
│   └── MessageBubble（三种类型）
│       ├── USER → 右对齐，蓝色
│       ├── AGENT → 左对齐，带头像
│       └── SYSTEM → 居中，灰色
├── #input-area
│   ├── AgentSelector（下拉选择 @ 谁）
│   ├── InputBox（消息输入）
│   └── Button（发送）
├── MentionPopup（@ 自动补全弹窗）
└── CommandPalette（按 / 打开，4 条命令）
```

**连接管理**: `CongressClient` 管理 WebSocket 连接，监听事件并推送到 ChatView。

**支持的命令**: `/clear` `/agents` `/help` `/quit`

---

## OperatorApp（`tlon ops`）

**架构**: IDE 风格多标签页

```
OperatorApp (Textual App)
├── NavigationBar（顶栏标签页切换：1-5）
├── Horizontal
│   ├── Sidebar（侧栏：Agent 列表）
│   │   ├── AgentTree（Agent 树状列表）
│   │   └── MiniEventStream（迷你事件流）
│   ├── Content Area（当前标签页内容）
│   │   ├── AgentsView（团队状态）
│   │   ├── EventsView（系统事件流）
│   │   ├── LogsView（执行日志）
│   │   ├── TracesView（调用链路）
│   │   └── ConfigView（配置查看）
│   └── InspectorPanel（选中项的详情面板）
└── StatusBar（底部状态）
```

**标签页切换**: 数字键 1-5，每个 `View` 是一个独立的 Textual `Widget`。

---

## 共享公共组件

两个 TUI 客户端共享 `common/components/`：

| 组件 | 用途 |
|------|------|
| `Header` | 自定义应用标题栏 |
| `StatusBar` | 底部状态栏 |
| `DataTable` | 数据表格展示 |
| `Dropdown` | 下拉选择器 |
| `Modal` | 模态弹窗 |
| `Toast` | 通知提示 |
| `TreeView` | 树状导航 |

---

## 连接生命周期

```
chat/ops 启动
  → CongressClient / OperatorClient 创建
  → WebSocket 连接 dream:8888
  → auth（含 username 人类身份）
  → subscribe(dream.events.*)
  → 用户 ↔ 消息 ↔ dream
  → Ctrl+C / /quit → 断开连接
```

用户名来源：`tlon chat --username alice` 命令行参数，未提供时启动交互提示
（回车默认 `guest`）；SDK 侧为 `authenticate(username=...)`。auth 成功后该
session 以 `human:<username>` 身份发言（见
[design/members.md](../design/members.md)）。

---

## 客户端数据流（以 Congress 为例）

### 发送消息

```
用户输入 → InputBox.on_submit()
  → CongressApp._handle_command() 或 _send_message()
  → CongressClient.send_message(content, target_agent)
  → WebSocket → dream
```

`target_agent` 来自 AgentSelector（当前选中的 Agent）或 InputBox 中的 `@mention`。

### 接收事件

```
WebSocket → CongressClient.on_event()
  → CongressApp._dispatch_event(event)
  ├── message.* → ChatView.add_message() → MessageBubble
  ├── agent.*   → AgentSelector.update_status()
  └── system.*  → connection-status / toast 提示
```

### @mention 流程

```
InputBox 输入 @
  → CongressApp.on_input_box_changed()
  → MentionPopup 弹窗（模糊匹配）
  → 选择 Agent → Insert @AgentName 到 InputBox
  → 按 Enter → 发送（含 @mention）
```

### 命令处理

```
输入 /clear → CongressApp._handle_command()
  → ChatView.clear_messages()

输入 /agents → CongressApp._handle_command()
  → CongressClient.request_agents()
  → 内联系统消息显示结果

输入 /help → CongressApp._handle_command()
  → 内联系统消息显示帮助

输入 /quit → CongressApp.exit()
```

---

## 参考

- [protocol.md](./protocol.md) — WebSocket 事件格式
- [dream.md](./dream.md) — 服务端架构
- [docs/archive/tui-congres.md](../archive/tui-congres.md) — Congress 设计文档（已退役）
- [docs/design/tui-operator.md](../design/tui-operator.md) — Operator 设计文档
