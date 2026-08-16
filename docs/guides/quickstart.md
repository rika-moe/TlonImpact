# Tlon 快速开始

从零跑到「第一封信」的完整走查。目标：本机起一个 Tlon 服务端，用 Operator TUI 给团队里某个 agent 发一封邮件，收到回信。

## 前置条件

| 依赖 | 版本要求 | 用途 |
|---|---|---|
| Python + uv | Python ≥ 3.11，uv 已装 | 安装依赖、跑服务端 |
| Node.js | ≥ 22 | Operator TUI（TypeScript/Ink 应用） |
| Docker | 可选 | shell/filesystem 工具的执行沙箱；没有也能起服务，只是这些工具会返回错误 |
| LLM API Key | 见下 | 认知 agent 的推理后端（默认 Moonshot，OpenAI 兼容） |

## 1. 安装依赖

```bash
uv sync
```

如需 OpenTelemetry 导出（可选）：`uv sync --extra otlp`。

## 2. 配置环境变量

```bash
mkdir -p ~/.tlon && cp .env.example ~/.tlon/.env
```

编辑 `~/.tlon/.env`，至少填上：

```env
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.moonshot.cn/v1   # 默认 Moonshot；换别的 OpenAI 兼容端点改这里
OPENAI_MODEL_NAME=kimi-k2.5
```

`.env` 与数据同目录（`{TLON_HOME}/.env`，默认 `~/.tlon/.env`），不覆盖真实环境变量（`base/settings.py` 的 `_load_dotenv_files`，`override=False`）；没建 `.env` 服务端也能启动，只是 agent 处理邮件时调用 LLM 会失败。全部变量见 `reference/config-reference.md` 第 4 节。

## 3. 起服务端（终端 1）—— 空社会首启自动初始化

```bash
uv run tlon dream
# 或别名：uv run tlon server
```

- 默认绑定 `127.0.0.1:8080`（REST + SSE 单端口）；改端口用 `--rest-port`。
- `--lucid`（或 `-l`）开调试日志。
- 启动时会播种内置原型（`agents/prototypes.py` 的 `seed_prototypes`），并**若 `~/.tlon` 下没有任何 agent，自动从职位原型初始化标准团队**（`agents/bootstrap.py`：林知遥←developer / 江予白←ops / 沈清晏←pm / 顾言蹊←support / 苏晚晴←product / 谢云舒←data-analyst，含角色卡人格）——幂等，已有成员即跳过。日志见 `bootstrap_standard_team_ready`。
- 随后注册全部 agent，并给带 `:on :boot` 规则的成员发启动请求。

看到日志出现 `server_started` 即就绪。此时 `curl http://127.0.0.1:8080/health` 应返回 200。

## 4. 打开 Operator TUI（终端 2）

```bash
# 首次使用需要先构建 TUI（Node ≥ 22）：
cd apps/operator && npm install && npm run build && cd ../..

uv run tlon ops
```

`tlon ops`（别名 `tlon intrude` / `tlon tui`）启动 Operator 运维控制台，默认视图就是 **Mail**。常用参数：

- `--username <名字>`（或环境变量 `TLON_USERNAME`）设置你的邮件身份，默认 `guest`；
- `--host` / `--rest-port` 连到服务端（默认 `127.0.0.1:8080`）。

视图切换：数字键 `1`–`6` 对应 Mail / Agents / Events / Traces / Config / Metrics，`tab` 循环。邮件视图是人类的「信件主界面」：左栏线程列表（新→旧），右栏选中线程的邮件明细，`mail.delivered` 事件实时并入。

## 5. 发第一封信

在 Mail 视图按 `c` 打开写邮件，依次填四个字段（**回车**进入下一个，在 Content 上回车即发送；`esc` 放弃，↑↓ 换字段）：

| 字段 | 说明 |
|---|---|
| To | 收件人，逗号分隔多个。支持 **名字 / id / 完整地址**（如 `顾言蹊`、`gu-yanxi`、`gu-yanxi@tlon.internal`），服务端做名称解析 |
| Cc | 抄送，同上 |
| Subject | 主题（可留空） |
| Content | 正文（不能为空） |

发送是同步确认的：`POST /messages` 返回 `202` 受理（投递异步），TUI 收到真实 `mail_id` 后把邮件并入列表；失败则表单留在原地、错误回填到表单里。

给客服专员发一封，例如 To 填 `顾言蹊`，正文写「你好，帮我看看项目环境是不是正常」。几秒内她的回信会以 `mail.delivered` 事件帧出现在右栏——agent 是异步处理邮件的，回信经 SSE 推送，无需刷新。

## 6. 验证与下一步

- 回信会落到 `~/.tlon/human-{你的用户名}/mail/inbox.jsonl`（人类收件箱，`base/paths.py` 的 `member_mail_dir`）；离线时信件在此等待投递。
- 发信记录在 `~/.tlon/human-{你的用户名}/mail/sent.jsonl`。
- agent 收发的邮件在各自 `~/.tlon/{agent_id}/mail/`（`inbox.jsonl` / `sent.jsonl` / `inbox.cursor`）。
- 想看 agent 在做什么：切换到 Agents 视图看状态，Events 视图看实时事件流，Traces 视图看一次会话的完整追踪。

协议细节（端点、SSE 帧格式、curl/httpx 裸调）见 `reference/api-reference.md`；信件机制的设计取舍见 `design/mail.md`。

## 常见问题

- **`tlon ops` 报 Operator TUI not installed** — 先 `cd apps/operator && npm install && npm run build`。
- **agent 不回信** — 确认 (1) `.env` 的 `OPENAI_API_KEY` 有效；(2) To/CC 里真的写了对方（`behavior.tlon` 里 `:when` 门控通常要求收件人包含自己才响应）；(3) 服务端日志有报错。回信是异步的，最多等几秒到几十秒。
- **shell/filesystem 工具报 `Docker runtime not initialized`** — Docker daemon 没起来或不可用；不影响 agent 本体和邮件收发。
- **改了 `agent.yaml` 不生效** — 服务端启动时扫描一次，热重载走 `POST /admin/config/agents/{agent_id}/reload`（条件注册端点，见 `reference/api-reference.md`）。
