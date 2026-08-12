# 第三章：Operator 控制台与邮件模型

> 代表大会已经散会，代表们各自回到自己的办公室，门口挂上了信箱。

本文取代了旧的「Congress TUI」章节：那个单栏聊天界面（Congress）已经退役，人类与系统交互的唯一官方界面是 **Operator 控制台**（`tlon ops`，TypeScript/Ink 实现）。

---

## 邮件模型：五条基本规则

Tlon 内部没有聊天室，只有邮件系统。五条规则概括全部行为：

1. **显式收件人。** 每封邮件必须有至少一个可解析的收件人（To 或 Cc），否则服务端拒绝发布。没有"对空气说话"：agent 只处理自己邮箱里的邮件，看不到发给别人的信。
2. **每成员一个邮箱。** 每个 agent 和每个人类用户都有自己的邮箱（inbox + sent）。地址格式 `<agent-id>@tlon.internal`，人类是 `<username>@tlon.internal`。
3. **沉睡不丢信。** agent 空闲时会进入沉睡。寄给沉睡 agent 的邮件写入其邮箱并触发唤醒（需数秒）；agent 启动后扫描未读邮件，逐封处理。
4. **线程由头部维系。** 回复邮件携带 `in_reply_to`（上游邮件 id）和 `thread_id`（线程根邮件 id），同一 `thread_id` 的邮件构成一个线程。没有 `thread_id` 的新邮件自成线程根。
5. **错误有回音。** 无法解析的收件人地址会触发一封来自 `postmaster@tlon.internal` 的退信（主题 `Undeliverable: ...`），落入发件人邮箱并继承原线程。

地址解析接受三种写法：完整地址（`lin-zhiyao@tlon.internal`）、agent id（`lin-zhiyao`）、agent 显示名（`林知遥`），服务端统一解析去重。特殊地址 `all@tlon.internal` 展开为全部已注册 agent 加全部在线人类。

两个容易踩的边界：

- **人类必须在线才能收件。** 人类邮箱随会话创建；寄给不在线的用户名会得到 postmaster 退信。
- **邮件模型有 `forward_of` 字段（转发），但 UI 和工具入口尚未提供**，目前只在数据模型层面存在。

---

## 启动控制台

```bash
uv run tlon ops                          # 以 guest 身份连接本机服务端
uv run tlon ops --username alice        # 指定邮件身份（推荐）
uv run tlon ops --host 192.168.1.100 --rest-port 8080
```

| 选项 | 说明 | 默认 |
|------|------|------|
| `--username, -u` | 邮件身份，决定你的地址 `<username>@tlon.internal` | `guest`（可用环境变量 `TLON_USERNAME`） |
| `--host` | 服务端地址 | `127.0.0.1` |
| `--rest-port, -r` | HTTP API 端口（REST + SSE） | `8080` |

顶栏显示当前视图、连接状态（connecting / connected / reconnecting / disconnected）、服务端地址和你的 username。断线会自动重连并重新认证。

**全局按键**（写信表单激活时除外，表单独占键盘）：

| 按键 | 操作 |
|------|------|
| `1` – `5` | 直接切换到 mail / agents / events / traces / config |
| `tab` / `→` | 下一个视图 |
| `←` | 上一个视图 |
| `q` / `Ctrl+C` | 退出 |

---

## mail 视图（默认视图）

人类的邮件主界面。左栏线程列表，右栏选中线程的会话流。

### 线程聚合语义

- 邮件按 `thread_id` 聚合为线程，左栏按**最新一封邮件的时间**倒序排列，每项显示参与者、主题（或正文前 24 字符）和邮件数。
- 右栏按时间正序显示线程内全部邮件：每封依次是 `发件人 → 收件人（含 (cc) 标记）· 时间`、`Subject:` 行、正文。
- 数据来源有两路，按邮件 id 去重合并：启动时从 REST `GET /inbox?username=...` 拉取（inbox + sent 合并），之后实时并入 SSE 事件流的 `mail.delivered` 事件。服务端**每个收件人发出一份投递事件**，去重保证了不会出现重复条目。
- 视图只显示**与你有关**的邮件（你是发件人或收件人之一）——这是客户端侧的呈现过滤。

### 写信（`c`）与回复（`r`）

- `c` 打开写信表单，四个字段 `To / Cc / Subject / Content`。回车前进到下一字段；在 Content 上回车提交；`esc` 取消；`↑↓` 也可在字段间移动。To/Cc 支持逗号分隔多个收件人，激活 To/Cc 字段时表单下方显示当前候选名单（`candidates:` 行）。
- `r` 回复选中的线程：自动取该线程**最新一封来信**，预填 To（来信发件人）、`Re:` 前缀主题，并携带 `in_reply_to` / `thread_id` 头部——回复自动继承线程。光标直接落在 Content。若线程里没有别人的来信，提示「该线程没有可回复的邮件」。
- 表单校验：To 为空、Content 为空都会被拦下并提示。

### 发送回执与错误提示

发信走 REST `POST /messages`，有同步回执：

1. 提交后表单显示 `sending…`，等待服务端响应。
2. 服务端返回 4xx（比如"没有可送达的收件人"）时，错误直接显示在表单内，**字段内容保留**，你可以改正后重发。
3. 202 回执（含 `mail_id`）后邮件追加到本地列表（发件人是你自己的邮件不会再以投递事件回推，所以必须本地补一条）。
4. 之后到达的、与本会话相关的服务端事件以提示条形式显示在视图标题行。

---

## agents / events / traces / config 四视图

| 视图 | 按键 | 用途 |
|------|------|------|
| **agents** | `↑↓` 选择 · `enter` 详情 · `r` 刷新 | 全部 agent 的 id、名字和状态（绿=运行中，红=停止/错误，黄=其他）。`enter` 拉取单个 agent 的详情 JSON。 |
| **events** | `↑↓` 滚动 · `p` 暂停 · `c` 清空 | 实时事件流（保留最近 500 条），每条显示时间、事件类型、来源、trace 前 8 位。滚动到底部自动跟随新事件；右侧为选中事件的完整 JSON。排查"刚才系统里发生了什么"的第一现场。 |
| **traces** | `↑↓` 选择 · `enter` 详情 · `r` 刷新 | 调用链列表。`enter` 拉取完整 trace（含 span 树）——一封信从发出、投递、唤醒 agent 到 LLM 调用的全链路都在这里。 |
| **config** | `r` 刷新 | 只读显示服务端配置和全部 agent 配置（identity 等），用于确认热加载后的生效值。 |

典型排查路径：在 mail 里发出一封信 → events 里看 `mail.delivered` 和 `agent.*` 事件 → 拿 trace id 到 traces 里看完整链路。

---

## 会话模式与可见性：为什么控制台能看到所有邮件

会话有三种模式（`POST /sessions` 声明初始模式，`PATCH /sessions/{id}` 切换），模式决定服务端推送哪些事件：

| 模式 | 推送的事件 |
|------|-----------|
| `chat` | `mail.*`、agent 响应与生命周期、`system.*`；其中 `mail.delivered` 由 HumanAdapter 按收件人投递（只到达收件人本人的会话），不经 NotificationService |
| `event` | 全部事件类型，**能看到全部邮件流量** |
| `agent` | 仅 `agent.*` |

Operator 控制台在认证成功后自动切换到 **`event` 模式**——它是运维控制台，设计上就要看到全部邮件流量和全部内部事件（LLM 请求、工具执行等）。这意味着：

- 你在 events 视图里能看到**别人之间**的邮件投递事件（比如顾言蹊转派给林知遥的信）——这是有意的，运维可见性优先。
- mail 视图不受影响：它客户端侧只呈现与你有关的邮件，数据口径仍是你的邮箱。
- 如果你用 SDK 写一个普通终端客户端（`chat` 模式），它就只收到写给自己的信。

模式之上还可叠加会话过滤器（`event_types` / `exclude_event_types` / `trace_id` / `agent`，支持 `prefix.*` 通配），过滤只在服务端执行。详见 [命令参考 · 会话与事件流（SSE）](../engineering/commands.md)。

---

## 多用户接入

多个人可以同时连同一个服务端，各自用不同的 `--username`：

```bash
# 终端 A
uv run tlon ops --username alice
# 终端 B（同一台或另一台机器）
uv run tlon ops --username alice --host 192.168.1.100
```

- 每人一个独立邮箱和身份：`alice@tlon.internal`、`alice@tlon.internal`。同名会话共享同一个身份和邮箱。
- 人类之间可以互相写信（对方在线时），也可以和 agent 混在同一个线程里（To/Cc 任意组合）。
- `all@tlon.internal` 的展开包含所有**在线**人类——全员通告会到达每一个连着的人。

---

## 下一步

- [第四章：记忆](../memory.md) — agent 如何记住你
- [第六章：镜子](../agents.md) — 创造新的 agent

---

## 🔧 工程参考

REST 端点、SSE 事件流、SDK → [命令参考](../engineering/commands.md) · [系统架构](../engineering/architecture.md)

---

> **观察练习**
>
> 开两个终端（`dream` + `ops`），在控制台里完成一次完整的链路观察：
> 1. mail 视图给 `jiang-yubai@tlon.internal` 写一封信
> 2. events 视图观察 `mail.delivered`、`agent.thinking_*`、`agent.llm_*` 事件的顺序
> 3. 记下事件的 trace id，到 traces 视图按 `enter` 看完整 span 树
> 4. agents 视图确认江予白的状态从沉睡转为运行
