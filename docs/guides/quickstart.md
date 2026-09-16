# Tlon 快速开始

从零跑到「第一封信」的完整走查。目标：本机起一个 Tlon 服务端，用 Operator TUI 给团队里某个 agent 发一封邮件，收到回信。

## 前置条件

| 依赖 | 版本要求 | 用途 |
|---|---|---|
| Python + uv | Python ≥ 3.11，uv 已装 | 安装依赖、跑服务端 |
| Node.js | ≥ 22 | Operator TUI（TypeScript/Ink 应用） |
| Docker | 可选 | shell/filesystem 工具的执行沙箱；没有也能起服务，只是这些工具会返回错误 |
| LLM API Key | 见下 | 认知 agent 的推理后端；按所用 provider 各配各的（`{PROVIDER}_API_KEY`） |

## 1. 安装依赖

```bash
uv sync
```

如需 OpenTelemetry 导出（可选）：`uv sync --extra otlp`。

## 2. 配置环境变量

`~/.tlon/.env`（与数据同目录；模板见仓库根的 `.env.example`）按 provider 填 API key——用哪家的模型配哪家的（litellm 原生约定 `{PROVIDER}_API_KEY`，provider 段大写、`-` 换 `_`）：

```env
MOONSHOT_API_KEY=your-api-key-here
# DEEPSEEK_API_KEY=...    # 用 deepseek 的模型就配这个
# ANTHROPIC_API_KEY=...
# OPENAI_API_KEY=...
```

`.env` 不覆盖真实环境变量（[base/settings.py](../../src/tlon/base/settings.py) 的 `_load_dotenv_files`，`override=False`）；没配 key 服务端也能启动，只是 agent 处理邮件时调用 LLM 会失败（报错会指明缺哪个变量）。**模型不在进程级配置**（ADR 0016）：每个成员的模型在创建时选择，落在成员 `agent.yaml` 的 `llm.model_name`。全部变量见 `reference/config-reference.md` 第 4 节。

## 3. 起服务端（终端 1）—— 空社会 + 原型库

```bash
uv run tlon dream
```

- 默认绑定 `127.0.0.1:8080`（REST + SSE 单端口）；改端口用 `--rest-port`。
- `--lucid`（或 `-l`）开调试日志。
- 启动时播种内置原型与技能（[agents/bootstrap.py](../../src/tlon/agents/bootstrap.py)）——**首次启动是空社会，不再自动初始化标准团队**（ADR 0016）；成员由人类经 WebUI / admin API 创建。

看到日志出现 `server_started` 即就绪。此时 `curl http://127.0.0.1:8080/health` 应返回 200。

## 3.5 创建第一个成员（WebUI）

浏览器打开 `http://127.0.0.1:8080/ui/`（需先 `cd apps/webui && npm install && npm run build`）：AGENTS 视图 → CREATE → 选原型（如 support 客服）→ 填名字、**模型**（`provider/model` 形式，如 `deepseek/deepseek-chat`，对应第 2 步配的 key）→ CREATE。成员即刻热加载上线。

## 4. 打开 Operator TUI（终端 2）

```bash
# 首次使用需要先构建 TUI（Node ≥ 22）：
cd apps/operator && npm install && npm run build && cd ../..

uv run tlon ops
```

`tlon ops` 启动 Operator 运维控制台，默认视图就是 **Mail**。常用参数：

- `--username <名字>`（或环境变量 `TLON_USERNAME`）设置你的邮件身份，默认 `guest`；
- `--host` / `--rest-port` 连到服务端（默认 `127.0.0.1:8080`）。

视图切换：数字键 `1`–`6` 对应 Mail / Agents / Events / Traces / Config / Metrics，`tab` 循环。邮件视图是人类的「信件主界面」：左栏线程列表（新→旧），右栏选中线程的邮件明细，`mail.delivered` 事件实时并入。

## 5. 发第一封信

在 Mail 视图按 `c` 打开写邮件，依次填四个字段（**回车**进入下一个，在 Content 上回车即发送；`esc` 放弃，↑↓ 换字段；**tab** 在 To/Cc 字段自动补全收件人——显示名优先、重名自动用 id）：

| 字段 | 说明 |
|---|---|
| To | 收件人，逗号分隔多个。支持 **名字 / id / 完整地址**（如 `顾言蹊`、`gu-yanxi`、`gu-yanxi@tlon.internal`），服务端做名称解析 |
| Cc | 抄送，同上 |
| Subject | 主题（可留空） |
| Content | 正文（不能为空） |

发送是同步确认的：`POST /messages` 返回 `202` 受理（投递异步），TUI 收到真实 `mail_id` 后把邮件并入列表；失败则表单留在原地、错误回填到表单里。

给第 3.5 步创建的成员发一封，例如 To 填她的名字（或输入首字后按 tab 补全），正文写「你好，帮我看看项目环境是不是正常」。几秒内回信会以 `mail.delivered` 事件帧出现在右栏——agent 是异步处理邮件的，回信经 SSE 推送，无需刷新。线程多了可按 `/` 过滤（参与者/主题/正文），未读线程有 `●` 标记。

## 6. 验证与下一步

- 回信会落到 `~/.tlon/human-{你的用户名}/mail/inbox.jsonl`（人类收件箱，[base/paths.py](../../src/tlon/base/paths.py) 的 `member_mail_dir`）；离线时信件在此等待投递。
- 发信记录在 `~/.tlon/human-{你的用户名}/mail/sent.jsonl`。
- agent 收发的邮件在各自 `~/.tlon/{agent_id}/mail/`（`inbox.jsonl` / `sent.jsonl` / `inbox.cursor`）。
- 想看 agent 在做什么：切换到 Agents 视图看状态，Events 视图看实时事件流，Traces 视图看一次会话的完整追踪。

协议细节（端点、SSE 帧格式、curl/httpx 裸调）见 `reference/api-reference.md`；信件机制的设计取舍见 `design/mail.md`。

## 常见问题

- **`tlon ops` 报 Operator TUI not installed** — 先 `cd apps/operator && npm install && npm run build`。
- **agent 不回信** — 确认 (1) 成员 `agent.yaml` 配了 `llm.model_name`（provider/model 形式）且 `.env` 里对应 provider 的 `{PROVIDER}_API_KEY` 有效（如模型是 `deepseek/...` 就配 `DEEPSEEK_API_KEY`）；(2) To/CC 里真的写了对方（`behavior.tlon` 里 `:when` 门控通常要求收件人包含自己才响应）；(3) 服务端日志有报错。回信是异步的，最多等几秒到几十秒。
- **shell/filesystem 工具报 `Docker runtime not initialized`** — Docker daemon 没起来或不可用；不影响 agent 本体和邮件收发。
- **改了 `agent.yaml` 不生效** — 服务端启动时扫描一次，热重载走 `POST /admin/config/agents/{agent_id}/reload`（条件注册端点，见 `reference/api-reference.md`）。