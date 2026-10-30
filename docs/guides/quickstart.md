# Tlon 快速开始

从零跑到「第一封信」的完整走查。目标：本机启动 Tlon，在浏览器中认识初始成员、发送第一封邮件并收到回信。

## 前置条件

| 依赖 | 版本要求 | 用途 |
|---|---|---|
| uv | 已安装（Python ≥ 3.11 由 uv 自动提供） | 安装并运行 Tlon |
| Docker | 可选 | Shell 及 workspace 外文件访问的沙箱；自己的 workspace 文件读写无需 Docker |
| LLM API Key | 见下 | 成员调用模型时使用的推理后端；按所用 provider 各配各的（`{PROVIDER}_API_KEY`） |

Windows 普通用户的 ZIP 解压启动流程见 [Windows 便携版](windows-portable.md)；本页继续说明 uv/CLI 入口。便携构建与验收设计见 [ADR 0053](../decisions/0053-portable-windows-distribution.md)，实际可下载版本以发布记录为准。Windows 原生运行的文件锁、服务生命周期及 workspace 实现见 [Windows 适配设计](../design/windows-support.md)。Windows workspace 需位于固定本地磁盘，不支持 UNC 或映射网络盘；Docker 仍为可选。Windows CI 不等同于 Windows 10 实机验收。

## 1. 安装

```bash
uv tool install --prerelease allow tlon-impact
```

升级：`uv tool upgrade --prerelease allow tlon-impact`。P5 的旧 v1 原型处置与内置包更新见 [P5 升级说明](../releases/p5.md)；播种不会覆盖已有目录。

## 2. 启动 Tlon

```bash
tlon dream
```

- 默认绑定 `127.0.0.1:8080`（REST + SSE 单端口）；改端口用 `--rest-port`。
- `--lucid`（或 `-l`）开调试日志。
- 启动时播种内置原型与技能。**全新实例首启自动创建 30 位中文人物成员**，带稀疏联系和五个初始组织（[初始社会](../design/initial-society.md)，ADR 0036）。已有实例不追加成员；重启、升级不会覆盖人格或复活已删除成员。

看到日志出现 `server_started` 即就绪。此时 `curl http://127.0.0.1:8080/health` 应返回 200。

## 3. 在浏览器连接模型

打开 [Tlon WebUI](http://127.0.0.1:8080/ui/)（静态资源随包分发，无需 Node）。首次设置用户名后，未配置模型凭据的实例显示“连接模型服务”。选择服务商、模型，填写 API Key，点击“验证连接并保存”。默认选择 DeepSeek 的 `deepseek/deepseek-flash`；验证会发送简短模型请求，可能产生少量费用。

普通 CLI 与 Windows 便携版使用同一向导。凭据保存到当前实例的 `.env`：普通 CLI 默认 `~/.tlon/.env`，自定义 `TLON_HOME` 时随该目录，便携版为程序旁的 `data/.env`。首次配置会应用到初始社会的成员，无需手动修改文件。以后在 **SETTINGS** 中更新凭据，或明确选择要换模型的成员；已运行成员更换模型需按页面提示重启 Tlon。

已经通过环境变量或实例 `.env` 配置至少一位成员模型凭据的用户直接进入，不会被强制重新验证或覆盖模型。其他缺少凭据的成员仍需分别配置。

## 4. 查看成员

全新实例的 **AGENTS** 视图列出 30 位人物，可以直接选择通信。也可以通过 **AGENTS → CREATE** 创建自己的成员，选择工作方式、填写名字和模型。模型仍属于各个成员，不是全局统一设置。

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
- **Shell 工具不可用或 workspace 外文件访问报 `Docker runtime not initialized`** — Docker daemon 没起来或不可用；装配时 Docker 初始化失败不会注册 Shell 工具，启动 Docker 后需重新装配成员（例如重启 Tlon）；不影响 agent 本体和邮件收发。
- **改了 `agent.yaml` 不生效** — 服务端启动时扫描一次，热重载走 `POST /admin/config/agents/{agent_id}/reload`（条件注册端点，见 `reference/api-reference.md`）。
- **访问地址限制** — 仅支持本机回环地址 `127.0.0.1`、`localhost`、`::1`，不支持局域网或公网部署；非回环绑定会直接启动失败。WebUI 首次设置实例 username 后自动进入，不设密码或 admin token；浏览器来源边界见 [ADR 0033](../decisions/0033-local-webui-identity.md)。

## 可选：手动配置凭据

熟悉命令行的用户仍可在启动前设置环境变量，或编辑当前实例的 `.env`，例如 `DEEPSEEK_API_KEY=your-api-key`。变量名是模型名称中服务商部分大写、`-` 换 `_`，再加 `_API_KEY`。完整模板见仓库根目录 `.env.example`。

启动时真实环境变量优先于 `.env`。在网页明确保存新凭据时，会同时更新实例文件与当前进程；下次启动仍遵循环境变量优先规则。凭据存在只用于判断是否需要首次向导，不代表已验证服务商账户可用。

## 前端开发环境

WebUI 的开发、构建和 CI 基线为 Node.js 24，包声明 Node ≥ 24 并使用 24.x 的 Node 类型定义。普通用户访问已构建的 WebUI 不需要安装 Node。GitHub Actions 自身的运行时由 Action 版本决定，与 `setup-node` 为项目安装的版本分别维护。
