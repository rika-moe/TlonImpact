# Tlon 快速开始

从零跑到「第一封信」的完整走查。目标：本机启动 Tlon，在浏览器中认识初始成员、发送第一封邮件并收到回信。

## 前置条件

| 依赖 | 版本要求 | 用途 |
|---|---|---|
| uv | 已安装（Python ≥ 3.11 由 uv 自动提供） | 安装并运行 Tlon |
| Docker | 可选 | Shell 及 workspace 外文件访问的沙箱；自己的 workspace 文件读写无需 Docker |
| LLM API Key | 见下 | 成员调用模型时使用的推理后端；按所用 provider 各配各的（`{PROVIDER}_API_KEY`） |

## 1. 安装

```bash
uv tool install tlon-impact
```

升级：`uv tool upgrade tlon-impact`。P5 的旧 v1 原型处置与内置包更新见 [P5 升级说明](../releases/p5.md)；播种不会覆盖已有目录。

## 2. 配置环境变量

`~/.tlon/.env`（与数据同目录；模板见仓库根的 `.env.example`）按 provider 填 API key——用哪家的模型配哪家的（litellm 原生约定 `{PROVIDER}_API_KEY`，provider 段大写、`-` 换 `_`）：

```bash
mkdir -p ~/.tlon
echo 'DEEPSEEK_API_KEY=your-api-key-here' >> ~/.tlon/.env
# MOONSHOT_API_KEY=...    # 用 moonshot 的模型就配这个
# ANTHROPIC_API_KEY=...
# OPENAI_API_KEY=...
```

`.env` 不覆盖真实环境变量（[base/settings.py](../../src/tlon/base/settings.py) 的 `load_process_env`，`override=False`）；没配 key 服务端也能启动，只是 agent 处理邮件时调用 LLM 会失败（报错会指明缺哪个变量）。**模型不在进程级配置**（ADR 0016）：每个成员的模型在创建时选择，落在成员 `agent.yaml` 的 `llm.model_name`。全部变量见 `reference/config-reference.md` 第 4 节。

## 3. 起服务端 —— 初始社会 + 原型库

```bash
tlon dream
```

- 默认绑定 `127.0.0.1:8080`（REST + SSE 单端口）；改端口用 `--rest-port`。
- `--lucid`（或 `-l`）开调试日志。
- 启动时播种内置原型与技能。**全新实例首启自动创建 30 位中文人物成员**，带稀疏联系和五个初始组织（[初始社会](../design/initial-society.md)，ADR 0036）。已有实例不追加成员；重启、升级不会覆盖人格或复活已删除成员。

看到日志出现 `server_started` 即就绪。此时 `curl http://127.0.0.1:8080/health` 应返回 200。

## 4. 查看初始成员或创建成员（WebUI）

浏览器打开 http://127.0.0.1:8080/ui/（静态资源随包分发，无需 Node）。全新实例的 AGENTS 视图会列出 30 位人物，可以直接选择通信。也可以创建自己的成员：AGENTS 视图 → CREATE → 按行为选择原型（如「标准无执行迭代」）→ 填名字、**模型**（`provider/model` 形式，如 `deepseek/deepseek-chat`，对应第 2 步配的 key）→ CREATE。成员 ID 由系统生成（m-UUID），无需填写；原型显示名描述行为，不代表职业。成员即刻热加载上线。

## 5. 发第一封信

切到 WebUI 的 **Mail 视图**，给初始人物（如奥斯特罗姆）或自己创建的成员写一封信：To 填名字，也可以使用 AGENTS 中的实际 id 或完整地址，正文写「你好，帮我看看项目环境是不是正常」。

发送是同步确认的：`POST /messages` 返回 `202` 表示本地信箱已经提交，阅读与处理异步进行。几秒内回信会出现在线程里——agent 异步处理邮件，回信经 SSE 推送，无需刷新。

## 6. 验证与下一步

- 人类与 agent 的邮件统一存于 `~/.tlon/shared/mail.sqlite3`，通过客户端收件箱和成员邮件接口查询；本地成功表示已经入箱，离线不影响接收。
- 发件箱与收件箱是同一份邮件的成员视图，已读状态单独属于各收件人，不代表工作已完成。
- 任一无效或退休收件人会让整封投信被拒绝；已注册网关仅尝试投递，不保证入箱或外部接收。
- 旧 JSONL 邮箱原样保留为离线档案，新系统不读取或导入。
- 想看 agent 在做什么：WebUI 的 Agents 视图看状态，Events 视图看实时事件流，Logs/Traces 视图看一次会话的完整追踪。

协议细节（端点、SSE 帧格式、curl/httpx 裸调）见 `reference/api-reference.md`；信件机制的设计取舍见 `design/mail.md`。

## 常见问题

- **agent 不回信** — 确认 (1) 成员 `agent.yaml` 配了 `llm.model_name`（provider/model 形式）且 `.env` 里对应 provider 的 `{PROVIDER}_API_KEY` 有效（如模型是 `deepseek/...` 就配 `DEEPSEEK_API_KEY`）；(2) To/CC 里真的写了对方（`behavior.tlon` 里 `:when` 门控通常要求收件人包含自己才响应）；(3) 服务端日志有报错。回信是异步的，最多等几秒到几十秒。
- **Shell 或 workspace 外文件访问报 `Docker runtime not initialized`** — Docker daemon 没起来或不可用；不影响 agent 本体和邮件收发。
- **改了 `agent.yaml` 不生效** — 服务端启动时扫描一次，热重载走 `POST /admin/config/agents/{agent_id}/reload`（条件注册端点，见 `reference/api-reference.md`）。
- **访问地址限制** — 仅支持本机回环地址 `127.0.0.1`、`localhost`、`::1`，不支持局域网或公网部署；非回环绑定会直接启动失败。WebUI 首次设置实例 username 后自动进入，不设密码或 admin token；浏览器来源边界见 [ADR 0033](../decisions/0033-local-webui-identity.md)。

## 附录：其他客户端（源码场景）

WebUI 之外还有两个客户端，面向源码开发者，需 `git clone` 仓库后使用（见 [AGENTS.md](../../AGENTS.md)）：

- **Operator TUI**（`uv run tlon ops`）：终端运维控制台，邮件主界面 + 成员/原型管理，需 Node ≥ 22 构建（`cd apps/operator && npm install && npm run build`）。
- **Telegram bot**（`uv run tlon telegram`）：手机收发邮件，需 `uv sync --extra telegram` 并在 `.env` 配 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_USERS`。
