# Telegram 客户端设计（MVP）

> 2026-08-24 拍板。来源：issue （原为空标题占位，2026-08-24 完成必要性/可行性调研后立项 MVP）。

## 定位

Tlon 里人类是普通成员，经邮件网络与 agent 通信。现有人类通道：Operator TUI、WebUI。Telegram 客户端是第三条人类通道——解决的是**手机上收推送、随手回信**的参与体验问题，不是系统能力问题。它与愿景（人类退得越远越好）正交：不为 agent 社会增加任何新机制。

**目标**：手机上收发 Tlon 邮件、与 agent 通信的最小闭环。
**非目标**（留后续）：多用户、群组、富媒体/附件、成员/原型管理（留在 TUI/WebUI）。

## 可行性前提（调研结论）

协议 v2 对新客户端的接入面已完备，**服务端零改动**：

- bot 以 `client_type="telegram"` 建会话（服务端只校验非空字符串）；
- chat 模式经 HumanAdapter 单轨投递，bot 只收到发给自己身份 `human:<username>` 的 `mail.delivered`；
- 发信用 `POST /messages`，身份由 body 的 username 决定，无需活会话；
- SDK（`src/tlon/app/clients/sdk/`）自带 SSE 断线重连；
- 全链路出站连接（bot→Tlon 的 SSE/REST、bot→Telegram 的 polling），bot 无需公网 IP / webhook / 端口。

## 架构

```
手机 ⇄ Telegram 云 ⇄ [tlon telegram bot 进程] ⇄ [tlon dream 服务端]
                        PTB polling（出站）        SSE（收信）+ REST（发信），均出站
```

bot 进程 = python-telegram-bot（polling）+ 官方 SDK `TlonClient`（chat 模式），同一 asyncio 事件循环共存。

## 关键决策

- **D1 身份模型——单用户 MVP**。bot 绑定一个 Tlon 身份（`--username` / `TLON_USERNAME`）；Telegram 侧用 user_id 白名单鉴权（`TELEGRAM_ALLOWED_USERS`，逗号分隔），白名单外消息一律忽略。多用户（每 Telegram 用户映射不同 Tlon 身份）架构上可行（每身份一条会话 + 一个 HumanAdapter），留作后续扩展。
- **D2 发信交互——回复式为主，命令式为辅**。推送来信时记录 `telegram_message_id → mail_id` 映射（内存，重启降级）；用户对某条来信点 Telegram「回复」→ 自动带 `in_reply_to` / `thread_id` 发回该发件人；`/send <名字> <内容>` 发起新信（收件人 = 成员 local part）；`/inbox` 拉未读列表，启动时推未读摘要（离线回填）。
- **D3 代码位置**：`src/tlon/app/clients/telegram/`（Python 客户端，与 `sdk/` 同层）。不放 `apps/`——那里是独立 TS 工程。import-linter 加一条 `app.clients.telegram` 契约（只依赖 sdk / base / python-telegram-bot）。
- **D4 入口**：`tlon telegram`（launcher command，进程内直接跑，不经 subprocess）。参数复用 `--host` / `--rest-port` / `--username`。
- **D5 配置**：`~/.tlon/.env` 新增 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_ALLOWED_USERS`，经 `tlon.base.settings` 读取。未配置时 `tlon telegram` 拒绝启动并打印配置指引。
- **D6 依赖**：`python-telegram-bot` 走 optional extra（`uv sync --extra telegram`），不用此功能不装。
- **D7 SDK 补强**：`adapter.py` 的 `ClientType.TELEGRAM` 占位变现（TelegramAdapter 子类，chat 模式、filters 只收 `mail.*`）；`client.py` 补 `get_inbox()` 封装（`GET /inbox` 服务端现成）。

## 文件清单

新建：

- `src/tlon/app/clients/telegram/`——`bot.py`（PTB 应用 + 消息路由）、`reply_map.py`（消息↔邮件映射 + 白名单校验，纯逻辑可测）；`adapter.py` 不重复——PR1 的 `sdk/adapter.py` TelegramAdapter 直接复用
- `src/tlon/app/launcher/commands/telegram.py`
- `src/tests/unit/test_telegram_bot.py`（映射 / 白名单 / 发信组包单测；收发链路参考 `test_sdk.py` 的 in-process 集成模式，PTB 侧以 deliver seam 打桩）

修改（PR2）：`launcher/main.py`（注册命令）、`base/settings.py`（TELEGRAM_* 配置）、`pyproject.toml`（lint-imports 契约 + mypy override）、`README.md` / `AGENTS.md`（快速开始）。

PR1 已落地：`sdk/client.py`（`get_inbox()`）、`sdk/adapter.py`（TelegramAdapter）、pyproject `telegram` extra。`docs/design/mail.md`（已废弃一节的过时表述）另行处理。

## 错误处理与边界

- 白名单为空 / token 缺失 → 启动即拒绝，明确提示；
- 会话 409（同身份已有活跃连接）→ 报错提示「同 username 已在别处连接」；
- SSE 重连由 SDK 兜底；PTB polling 断线库内自恢复；
- 回复映射丢失（重启后回复旧消息）→ 降级为无 `in_reply_to` 的普通发信，不报错。

## 验收

端到端冒烟：起 dream → `tlon telegram` → 手机 `/send 顾言蹊 …` → agent 收到并回信 → 手机收到推送 → 对该推送回复 → agent 收到带 `in_reply_to` 的信。另加八件套全绿。
