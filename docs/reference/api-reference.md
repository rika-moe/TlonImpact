# API 参考（REST + SSE，协议 v2）

Tlon 服务端对外的完整协议面。实现：[src/tlon_server/api/rest/](../../src/tlon_server/api/rest) 包的 `RESTServer` / `RESTAPIHandler`（`rest/app.py`；路由域按模块拆分）。设计理由见 [docs/design/server-protocol.md](../design/server-protocol.md)，本文只描述当前实现。

## 总述

| 项 | 值 | 出处 |
|---|---|---|
| Base URL | `http://127.0.0.1:8080`（默认；host/port 由 `RESTServer` 构造参数决定） | `rest/app.py` 的 `RESTServer` |
| 协议版本 | `SESSION_PROTOCOL_VERSION = "2.0.0"`，`POST /sessions` 唯一接受的版本 | `rest/sessions.py` |
| 身份与接入 | 本地单人、无账号认证；WebUI 首次设置实例 username，服务端决定邮件与会话身份；业务 API 统一检查浏览器来源 | [ADR 0033](../decisions/0033-local-webui-identity.md) |
| 响应格式 | 除 SSE 外全部是 JSON（`application/json`，`ensure_ascii=False`） | `rest/common.py` 的 `_json_response` |
| 来源边界 | 仅回环监听，核对 Host/Origin/Fetch Metadata；业务 API 要求 `X-Tlon-WebUI: 1`（SSE 例外见下）；所有响应不输出 CORS 授权头，拒绝业务 API 预检 | `rest/browser_boundary.py` 与 `rest/app.py` |

### 错误响应

带 reason 的 4xx/503 错误体为 `{"error": <给人看的消息>, "reason": <机器可读枚举>}`，客户端分支判 `reason`。`reason` 全量枚举：

| reason | 触发端点 | 含义 |
|---|---|---|
| `invalid_json` | `POST /identity`、`POST /messages`、`POST /sessions`、`PATCH /sessions/{id}`、`POST /inbox/read` | 请求体不是合法 JSON；messages/sessions 的 POST 与 session PATCH 也用此码表示非 JSON object |
| `invalid_body` | `POST /inbox/read` | JSON 语法有效，但请求体不是 object |
| `invalid_read` | `POST /inbox/read` | read 不是严格布尔值 |
| `invalid_selector` | `POST /inbox/read` | 未恰好提供 mail_id 或 thread_id 中的一个 |
| `invalid_mail_id` | `POST /inbox/read` | mail_id 非字符串或为空白 |
| `invalid_limit` | `GET /inbox`、`GET /members/{member_id}/mail` | limit 是零或负数，在游标校验前拒绝 |
| `invalid_cursor` | `GET /inbox`、`GET /members/{member_id}/mail` | before 为空、邮件不存在或不属于该可见邮箱 |
| `invalid_member_id` | `GET /members/{member_id}/mail` | member_id 为空或含路径分隔符 |
| `mail_not_found` | `POST /inbox/read` | 目标没有该人类的收件关系，404（仅发件副本不可标读） |
| `member_not_found` | `GET /members/{member_id}/mail` | 运行配置与历史邮件库中均无该成员，404 |
| `invalid_username` | `POST /identity` | username 不符合 1–64 位 ASCII local-part 规则，或使用保留名 |
| `identity_conflict` | `POST /identity` | 与现有 agent id 撞名（不区分大小写），409 |
| `identity_exists` | `POST /identity` | 实例身份已经设置，409 |
| `identity_write_failed` | `POST /identity` | 身份文件写入失败，503 |
| `identity_required` | 除公开入口、identity 与 GitHub webhook 外的业务 API | 尚未设置实例身份，409 |
| `identity_managed` | `POST /messages`、`POST /sessions`、`GET /inbox`、`POST /inbox/read` | 请求仍携带旧 username 字段或参数，400 |
| `untrusted_origin` | 全部端点的浏览器边界检查；业务 API 预检 | Host/Origin/Fetch Metadata 不可信、缺少 WebUI 请求头或不允许的预检，403 |
| `invalid_client_type` | `POST /sessions` | client_type 缺失、非字符串或为空白 |
| `invalid_mode` | `POST /sessions`、`PATCH /sessions/{id}` | mode 不在 `SESSION_MODES = ("chat", "event", "agent")` 内 |
| `invalid_filters` | `POST /sessions`、`PATCH /sessions/{id}` | filters 不是 object |
| `unsupported_protocol_version` | `POST /sessions` | protocol_version ≠ "2.0.0"；响应额外带 `supported_versions` 列表 |
| `empty_content` | `POST /messages` | content 缺失、非字符串或全空白（`send_mail.py` 的 `parse_send_mail_fields`） |
| `invalid_to` / `invalid_cc` | `POST /messages` | to/cc 存在但不是 list |
| `invalid_in_reply_to` | `POST /messages` | in_reply_to 存在但不是字符串 |
| `invalid_thread_id` | `POST /messages`、`POST /inbox/read` | thread_id 存在但不是字符串；标读接口也拒绝空白 |
| `invalid_recipients` | `POST /messages` | 任一 to/cc 地址无效或成员已退休，整封拒绝并返回 invalid_recipients 列表 |
| `invalid_request_id` | `POST /messages` | request_id 缺失、非字符串或空白 |
| `idempotency_conflict` | `POST /messages` | 同发件人、同 request_id 被用于不同内容，409 |
| `submission_unknown` | `POST /messages` | 存储或传输结果未知，使用原 request_id 重试确认 |
| `stream_active` | `GET /events` | 该会话已有一条活跃 SSE 流 |

两个不一致处，如实说明：

- 404 响应（`GET /agents/{id}`、`GET /traces/{id}`、`PATCH`/`GET /events` 的会话不存在等）只有 `{"error": ...}`，**没有 `reason` 字段**。
- 503 响应（依赖未装配时）形状是 `{"status": "error", "message": ...}`，与 4xx 的 `{"error", "reason"}` 不同。

### 本地实例身份与浏览器来源边界（ADR 0033）

无密码、认证会话或 admin token。唯一 username 由 WebUI 首次设置并保存在实例的 `webui-identity.json`，换浏览器或重启不会改变。邮件发件人、自己的邮箱与 SSE session 身份均由服务端从该文件决定；旧 guest 的邮件与关系不关联、不迁移。

服务端只允许监听 `127.0.0.1`、`localhost` 或 `::1`，请求 Host 必须是实际监听端口上的回环 authority。Origin 若存在必须同源；`Sec-Fetch-Site` 仅允许缺失、`same-origin` 或 `none`。静态页与 `/health` 无需 WebUI 请求头，其余业务 API（含 `/identity`、`/admin/*`）必须携带 `X-Tlon-WebUI: 1`。原生 EventSource 不能加头，仅 `GET /events` 可用 `Sec-Fetch-Site: same-origin` 替代。边界失败统一返回 403 `untrusted_origin`；跨域预检不放行，所有响应（含 SSE、错误和静态资源）不输出 CORS 授权头，并禁止页面被嵌入。

身份未设置时，业务 API 返回 409 `identity_required`；公开入口、identity API 与 GitHub webhook 例外。`POST /webhooks/github` 沿用自身 HMAC 校验，不要求 WebUI 头，但仍经过 Host/Origin/Fetch Metadata 检查。此边界隔离非同源网页，不隔离本机程序。当前只保证 WebUI；TUI、SDK 与 Telegram 不提供兼容接入。

### 条件注册

路由在 `RESTAPIHandler.create_app` 集中注册，部分按装配结果条件挂载；未挂载时 aiohttp 返回默认的 404（非 JSON 错误体）：

| 路由 | 注册条件 |
|---|---|
| `GET /admin/config/agents`、`POST /admin/config/agents/{agent_id}/reload` | 注入了 `config_manager` |
| `GET /traces`、`GET /traces/{trace_id}` | 注入了 `trace_store` |
| `POST /webhooks/github` | 配置了 GitHub ingress 且 `GITHUB_WEBHOOK_SECRET` 非空 |
| `GET /ui`、`GET /ui/`（+ 静态资源） | WebUI 构建产物（apps/webui/dist 的 index.html）存在 |
| `GET /test/ping`、`POST /test/echo` | `debug=True`（仅冒烟用，生产不开启） |
| `GET /metrics` | 总是注册；无 metrics 服务时返回 503 |

## 端点总表

<!-- BEGIN GENERATED: rest-endpoints -->
`create_app` 共注册 53 条路由（按源码行号排序；「注册条件」是条件挂载的守卫，`无条件` = 总是注册）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 方法 | 路径 | handler | 注册条件 |
|---|---|---|---|
| GET | `/health` | `handle_health` | 无条件 |
| GET | `/identity` | `handle_identity` | 无条件 |
| POST | `/identity` | `handle_initialize_identity` | 无条件 |
| GET | `/config` | `handle_config` | 无条件 |
| GET | `/inbox` | `handle_inbox` | 无条件 |
| POST | `/inbox/read` | `handle_inbox_read` | 无条件 |
| GET | `/members/{member_id}/mail` | `handle_member_mail` | 无条件 |
| POST | `/messages` | `handle_send_message` | 无条件 |
| GET | `/agents` | `handle_list_agents` | 无条件 |
| GET | `/agents/{agent_id}` | `handle_get_agent` | 无条件 |
| GET | `/sessions` | `handle_list_sessions` | 无条件 |
| POST | `/sessions` | `handle_create_session` | 无条件 |
| PATCH | `/sessions/{session_id}` | `handle_update_session` | 无条件 |
| DELETE | `/sessions/{session_id}` | `handle_delete_session` | 无条件 |
| GET | `/events` | `handle_events` | 无条件 |
| GET | `/participants` | `handle_list_participants` | 无条件 |
| GET | `/admin/config/agents` | `handle_list_agent_configs` | configs |
| POST | `/admin/config/agents/{agent_id}/reload` | `handle_reload_agent` | configs |
| POST | `/admin/agents/{agent_id}/wake` | `handle_wake_agent` | 无条件 |
| POST | `/admin/agents/{agent_id}/wait` | `handle_wait_agent` | 无条件 |
| POST | `/admin/agents/{agent_id}/sleep` | `handle_sleep_agent` | 无条件 |
| POST | `/admin/agents/{agent_id}/terminate` | `handle_terminate_agent` | 无条件 |
| POST | `/admin/config/agents` | `handle_create_agent` | 无条件 |
| GET | `/admin/config/agents/{agent_id}` | `handle_get_agent_config` | 无条件 |
| PATCH | `/admin/config/agents/{agent_id}` | `handle_update_agent` | 无条件 |
| DELETE | `/admin/config/agents/{agent_id}` | `handle_delete_agent` | 无条件 |
| GET | `/admin/llm/models` | `handle_list_llm_models` | 无条件 |
| GET | `/admin/prototypes/staging` | `handle_list_staging` | 无条件 |
| POST | `/admin/prototypes/staging` | `handle_create_staging_prototype` | 无条件 |
| GET | `/admin/prototypes/staging/{name}` | `handle_get_staging_prototype` | 无条件 |
| PATCH | `/admin/prototypes/staging/{name}` | `handle_update_staging_prototype` | 无条件 |
| GET | `/admin/prototypes` | `handle_list_prototypes` | 无条件 |
| POST | `/admin/prototypes` | `handle_create_prototype` | 无条件 |
| POST | `/admin/prototypes/staging/{name}/approve` | `handle_approve_prototype` | 无条件 |
| POST | `/admin/prototypes/staging/{name}/reject` | `handle_reject_prototype` | 无条件 |
| GET | `/admin/prototypes/{name}` | `handle_get_prototype` | 无条件 |
| PATCH | `/admin/prototypes/{name}` | `handle_update_prototype` | 无条件 |
| DELETE | `/admin/prototypes/{name}` | `handle_delete_prototype` | 无条件 |
| POST | `/admin/prototypes/staging/{name}/arena-runs` | `handle_start_arena_run` | 无条件 |
| GET | `/admin/arena-runs` | `handle_list_arena_runs` | 无条件 |
| GET | `/admin/arena-runs/{run_id}` | `handle_get_arena_run` | 无条件 |
| GET | `/admin/prototypes/staging/{name}/arena-runs/{run_id}/report` | `handle_get_arena_report` | 无条件 |
| GET | `/traces` | `handle_list_traces` | observability |
| GET | `/traces/{trace_id}` | `handle_get_trace` | observability |
| GET | `/metrics` | `handle_metrics` | 无条件 |
| POST | `/webhooks/github` | `handle_github_webhook` | github_ingress + github_webhook_secret |
| GET | `/agents/{agent_id}/logs` | `handle_get_agent_logs` | 无条件 |
| GET | `/logs` | `handle_list_logs` | 无条件 |
| GET | `/test/ping` | `handle_test_ping` | debug |
| POST | `/test/echo` | `handle_test_echo` | debug |
| GET | `/ui` | `handle_webui_redirect` | webui dist 存在 |
| GET | `/ui/` | `handle_webui_index` | webui dist 存在 |
| GET | `/ui/` | `(static)` | webui dist 存在 |
<!-- END GENERATED: rest-endpoints -->

各端点的请求/响应细节见下文「端点详情」。

## 端点详情

### GET /health

无参数。200：

```json
{"status": "healthy", "version": "0.2.0"}
```

公开健康检查只返回存活状态与服务版本号，不暴露 runtime 状态或时间；`version` 与协议版本无关。

### GET /identity

无参数；要求 `X-Tlon-WebUI: 1`。200 返回 `{"username": null, "address": null}`（未设置）或 `{"username": "alice", "address": "alice@tlon.internal"}`。

### POST /identity

首次设置唯一实例身份。JSON 请求体为 `{"username": "alice"}`，成功 201 返回与 GET 相同的已设置对象。username 长 1–64 位，以 ASCII 字母或数字开头，后续只允许 ASCII 字母、数字、点、下划线、连字符；不自动改写大小写。保留名 `postmaster`、`adam`（不区分大小写）返回 400 `invalid_username`，与现有 agent id 撞名返回 409 `identity_conflict`。 创建、reload 或 PATCH 同名成员也返回 409 `identity_conflict`，在配置与人格文件写入及收养之前拒绝；冷启动发现持久身份与启动成员撞名时拒绝开始服务。

重复设置或并发初始化已有胜出者返回 409 `identity_exists`；非法 JSON 返回 400 `invalid_json`；非对象、缺 username 或非法名字返回 400 `invalid_username`；写入失败返回 503 `identity_write_failed`。失败不建立新身份；文件损坏时启动显式报错，不回落 guest。没有改名、删除身份、注册或登录接口。

### POST /messages

人类成员发信，处理函数 `rest/mail.py` 的 `handle_send_message`。发件人身份取自实例持久 username，不接受客户端自报身份；该人类没有在线 adapter 时由 `AdapterManager.publish_as` 建临时 adapter（首次接触同时创建持久邮箱），发完即拆。返回 **202** 前已在 SQLite 事务内完成全部本地入箱；不代表收件人已阅读或处理。已注册网关仅尝试投递其信箱，不保证成功或外部平台收到。

请求体字段（校验的唯一副本在 [tlon_server/api/send_mail.py](../../src/tlon_server/api/send_mail.py) 的 `parse_send_mail_fields`）：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `content` | string | 是 | 非空白；否则 400 `empty_content` |
| `request_id` | string | 是 | 一次投信的稳定非空标识；同发件人同键同内容返回已有回执，不同内容返回 409 |
| `to` | list[string] | 否 | 收件人：名字、成员 id 或完整地址；存在则必须是 list，否则 400 `invalid_to` |
| `cc` | list[string] | 否 | 同上，错误码 `invalid_cc` |
| `subject` | string | 否 | — |
| `in_reply_to` | string | 否 | 被回复邮件的 id；非字符串报 `invalid_in_reply_to` |
| `thread_id` | string | 否 | 线程根 id，缺省取本邮件自身 id；非字符串报 `invalid_thread_id` |

`username` 字段返回 400 `identity_managed`，其他未知字段静默忽略。`to`/`cc` 任一地址无效或成员已退休，整封拒绝，返回全部问题地址，不写成功发件记录也不另发退信。休眠或离线成员正常入箱。

- 202：`{"mail_id", "request_id", "local_recipients": [...], "gateways": [{"address", "status", "error"}], "message"}`。网关 status 为 delivered/failed/unknown，仅描述网关信箱，不是外部平台状态。
- 400：`{"error", "reason", "invalid_recipients"}`。
- 409：同 request_id 不同内容。
- 503：邮件能力不可用，或 `submission_unknown`；未知结果应以原 request_id 确认，不能换新键盲目重发。

### GET /inbox

读实例身份对应成员的持久邮箱，不接受 username query（携带则 400 `identity_managed`），处理函数 `handle_inbox`，调用 `MailFacade.inbox_page`。inbox 与 sent 合并，按 SQLite timestamp/rowid 稳定分页，页内按时间正序返回。读取不修改已读状态。

| Query 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 50（上限 200） | 非整数回落默认值 |
| `before` | string | 无 | 上一页最早邮件 id，读取更早记录；必须属于当前可见邮箱，否则 400 |

- 200：`{"mails": [...], "count": n, "has_more": bool, "next_before": string|null}`。`has_more` 为 true 时，`next_before` 是本页最早邮件 id；否则为 null。
- 400：零或负数 limit 返回 `invalid_limit`，先于游标校验；无效 before 返回 `invalid_cursor`。
- Mail 字段见 `Mail.to_dict`；额外含 `read: bool`，来自该人类的收件关系。仅发件记录显示为 true；发给自己的邮件按自己的收件关系判定。
- 503：未装配 mail facade。

### POST /inbox/read

显式设置人类收件关系的已读状态，处理函数 `handle_inbox_read`。本机来源边界与发信一致，身份来自实例持久配置；没有替任意 agent 修改状态的接口。

请求 JSON 必须包含 `read`（严格布尔值），以及 `mail_id` 或 `thread_id` 恰好一个非空字符串。线程操作在一个事务中覆盖该人类收到的所有该线程邮件，包括尚未加载的历史。仅发件副本不可操作，其他收件人的状态不会改变。

- 200：`{"mail_ids": ["..."], "read": bool}`，包含全部匹配的收件邮件 id（含原本已处于目标状态的邮件）。
- 400：`{"error": "...", "reason": "..."}`。reason 为 `invalid_json`（语法）、`invalid_body`（非对象）、`identity_managed`（携带 username）、`invalid_read`（非布尔值）、`invalid_selector`（未恰好指定一个范围）、`invalid_mail_id` 或 `invalid_thread_id`（空值/类型错误）。
- 404：没有匹配的收件关系，reason 为 `mail_not_found`。
- 503：未装配 mail facade。

### GET /members/{member_id}/mail

观察者视角读任意成员邮箱，处理函数 `handle_member_mail`，调用 `MailFacade.member_inbox_page`。`member_id` 使用 agent 裸 id 或人类 `human:<username>`。成员存在于运行注册表或历史邮件库即可以观察；未知成员返回 404。纯只读，不注册成员，不暴露 `read`。

Query 接受 `limit` 与 `before`，分页及响应元数据与 `GET /inbox` 相同，但邮件没有 `read` 字段。已知空邮箱返回空页。非法 member id、非正数 limit 或不可见游标返回 400，reason 分别为 `invalid_member_id`、`invalid_limit`、`invalid_cursor`；未知成员的 404 reason 为 `member_not_found`，未装配 facade 返回 503。`GET /inbox` 的无效游标同样返回 `invalid_cursor`。

### POST /sessions

建立客户端会话，身份由实例持久配置确定，携带 username 返回 400 `identity_managed`。这是协议版本协商的唯一检查点，处理函数 `handle_create_session`。只创建会话记录，**此时不注册事件处理器、不 attach adapter**——接线发生在 `GET /events` 开流时。

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `protocol_version` | string | 是 | 必须等于 `"2.0.0"`，否则 400 `unsupported_protocol_version`（响应带 `supported_versions`） |
| `client_type` | string | 否（默认 `"unknown"`） | 非空字符串，否则 400 `invalid_client_type`；会成为 session id 前缀 |
| `mode` | string | 否（默认 `"chat"`） | `chat` / `event` / `agent`，否则 400 `invalid_mode` |
| `filters` | object | 否 | 初始事件过滤器（见 SSE 节），非 object 报 400 `invalid_filters` |

- 201：会话状态 + 快照，字段 `session_id`、`username`、`client_type`、`mode`、`filters`、`connected_at`（ISO）、`participants`（`[{id, name, type}]`）、[agents](../../src/tlon/agents)（同 `GET /agents` 元素）。session id 由 `SessionManager.create_session` 生成，格式 `{client_type}-{四位计数}`，进程内单调，重启清零
- 400：`{"error", "reason"}`
- 会话状态全在内存（`SessionManager`），服务端重启即清空；客户端重连必须重新 POST

### GET /sessions

无参数。200：`{"sessions": [{id, client_type, connected_at, mode, filters}], "count": n}`。注意与 `POST /sessions` 响应不同：列表元素用 `id` 而非 `session_id`，且不含 `username`。

### PATCH /sessions/{session_id}

在线修改 mode / filters，立即生效，无需重开流，处理函数 `handle_update_session`。响应即更新后的会话状态，**不在 SSE 流上推确认帧**。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 否 | 同 `POST /sessions` 校验 |
| `filters` | object | 否 | **整体替换**会话的 filters，不是合并 |

- 200：会话状态（`session_id`、`username`、`client_type`、`mode`、`filters`、`connected_at`）
- 400：`invalid_json` / `invalid_mode` / `invalid_filters`
- 404：会话不存在（`{"error"}`，无 reason）

### DELETE /sessions/{session_id}

幂等断开，处理函数 `handle_delete_session`：给该会话的 SSE 队列放 `None` 哨兵结束流，再 detach adapter、关闭会话。删除不存在或已断开的会话返回同样的 200：`{"session_id": "<id>", "status": "disconnected"}`。

### GET /agents

无参数。200：`{"agents": [...], "count": n}`，元素来自 [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `get_agent_status`：

| 字段 | 说明 |
|---|---|
| `id` | agent id |
| `name` | 显示名 |
| `state` | 四态：`active`、`waiting`、`sleeping`、`dead`；根据实际工作、睡眠期限和永久死亡标记派生 |
| `wake_at` | 睡眠的 UTC ISO 到期时间，其余状态为 null |
| `scheduling_pending` | 当前等待/睡眠/退出意图；活跃且非空表示排空中，sleeping 的 sleep 表示已入睡 |
| `stop_pending` | 管理员永久退出正在排空 |
| `resident` / `waking` / `error` | 实例驻留、装配中、故障诊断；error 也包含按维护类别标记的退避/阻塞原因和损坏 checkpoint 路径，均不改变四态定义 |
| `prototype` | 行为原型 |
| `spawned_by` | spawn 者 id |
| `spawn_depth` | spawn 深度 |

### GET /agents/{agent_id}

单个 agent。200 返回与列表相同的权威状态字段，并附加 `config: {prototype, spawned_by, spawn_depth}`。等待实例即使尚未装配或已卸载仍为 waiting；死亡不可通过 reload/start 复活。404：agent 未注册（`{"error"}`，无 reason）。

### POST /admin/agents/{agent_id}/wake、wait、sleep、terminate

管理员调度控制，独立于邮件 API 与成员工具。按钮名称与状态转换见 [四态设计](../design/agent-scheduling-states.md#9-管理员状态操作)。

| 操作 | 请求 | 效果 |
|---|---|---|
| `wake` | 无请求参数 | 提前结束已有睡眠并检查真实邮箱；有可处理邮件才活跃，否则等待 |
| `wait` | 无请求参数 | 活跃成员排空当前工作后恢复正常调度；未读邮件仍可使其再次活跃 |
| `sleep` | JSON `{"wake_at": "2030-01-01T12:00:00+08:00"}` | 未来且带时区，归一 UTC；当前工作排空后睡眠，也可调整已有期限 |
| `terminate` | 无请求参数 | 排空当前工作后永久死亡，拒收新邮件并保留历史；不可撤销 |

200 表示请求已完成；202 表示已接受，仍在等待当前工作结束，此时成员仍可显示 active。响应包含 `status: success|pending`、`agent_id`、`stop_pending` 和权威 `agent` 快照；wake/wait/sleep 另带 `scheduling_pending`。SSE `agent.state_changed` 推送相同状态。不存在强制设置 active 的接口。202 中的管理等待/睡眠请求若遇服务重启会清除，应重新发起；已正式入睡后的期限跨重启保留，未结束模块程序按 ProgramStore 的冻结记录恢复。

无效睡眠时间、非对象 JSON、额外字段或格式错误返回 400；未知成员 404；死亡、永久退出中或冲突的转换请求返回 409。普通来信不提前唤醒。唤醒或修改期限会撤销旧计时器；死亡不会因旧回调、管理员唤醒或服务重启复活。

### GET /participants

成员目录全量（agent + 已知人类），来自 `ClientGateway.get_all_members`。200：`{"participants": [{id, name, type, metadata}], "count": n}`；人类成员 id 形如 `human:<username>`。

### GET /config

服务端配置摘要（脱敏）。200：

```json
{"server": {"host": "...", "port": 8080}, "agents": {"count": n}, "sessions": {"active": n}}
```

### GET /admin/config/agents（条件注册）

200：`{"status": "success", "agents": [{id, name, identity}], "count": n}`。未装配 config_manager 时路由不存在（404）。

### POST /admin/config/agents/{agent_id}/reload（条件注册）

热收纳磁盘上已存在的 agent 配置（成员创建类管理操作写完后调用），实际走 [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `adopt_agent`。200：`{"status": "success", "agent_id", "result"}`，`result` 枚举：

| result | 含义 |
|---|---|
| `already-known` | 已注册或在运行，不重启；但会从磁盘重载配置（覆盖进程外的 agent.yaml 编辑，如 WebUI 成员编辑），下次启动按新配置组装 |
| `config-not-found` | 磁盘上没有该 agent 的 `agent.yaml` |
| `registered-lazy` | 已注册，lazy 激活（等首封邮件唤醒） |
| `started` | 已注册并启动 |
| `start-failed` | 启动失败（细节见服务端日志） |

注意：`result` 为 `config-not-found` / `start-failed` 时 HTTP 状态仍是 200，调用方须检查 `result`。

### GET /admin/llm/models

模型元数据注册表（ADR 0016 §4），供 WebUI 创建/编辑成员表单的模型下拉使用。数据源是 litellm `model_cost` 注册表，只输出 `provider/model` 键形式且 `mode == "chat"` 的条目，按 provider、model 排序。200：`{"status": "success", "models": [...], "count": n}`；每条 `{model, provider, context_window, input_price, output_price, cache_read_price, supports_prompt_caching, supports_function_calling, deprecation_date}`——价格是每百万 token 美元（注册表的 per-token 值 × 1e6），注册表未收录的字段为 `null`。

### 原型管理端点（WebUI PROTOTYPES 视图）

域函数在 [agents/prototypes/](../../src/tlon/agents/prototypes) 包；原型对象形状 `{name, display_name, description, trusted, author, parent, generation, proposal_note, parameters: [{name, type, required}]}`（`parent`/`generation`/`proposal_note` 是谱系字段：`parent` 为父原型 ID、根为 `null`，`generation` 根=1 且由服务端推导，`proposal_note` 为本代变更说明）。详情端点额外带 `behavior`（behavior.tlon 全文）、`lessons`（`LESSONS.md` 全文，仅原型携带该文件时出现）、`reviews`（`REVIEW.md` 同行评审记录全文，仅原型携带该文件时出现——staging 提案经 `prototype-review` 评审累积，approve 后随目录晋级 trusted）与 `lineage`（`get_prototype_lineage` 的派生结果：根在前，每代 `{name, generation, author, proposal_note, trusted, parent, diff_from_parent, broken}`，`diff_from_parent` 是相邻两代 behavior.tlon 的 unified diff，根为 `null`；父原型已删/改名时链在断点截断并标 `broken`）。写路径一律先跑校验链（模板分析 → EDN parse → `validate_member_spec`），失败不落盘。

- `GET /admin/prototypes`、`GET /admin/prototypes/staging`——200：`{"status": "success", "prototypes": [<原型对象>]}`；503：未装配 `paths`
- `GET /admin/prototypes/{name}`、`GET /admin/prototypes/staging/{name}`——200：`{"status": "success", "prototype": <原型对象 + behavior + lessons? + reviews? + lineage>}`；404：`{"error", "reason": "prototype_not_found"}`
- `POST /admin/prototypes`——body `{behavior, display_name, description?, parent?, proposal_note?, lessons?}`，name 由系统生成 p-UUID，display_name 为必填非空行为标签；带旧 name 返回 400 managed_id。手写创建直接进正式库（`:trusted true`、无 author）；`parent` 须指向已存在的原型（trusted 或 staging 皆可），`generation` 服务端推导、不接受客户端指定；`lessons` 落盘为 `LESSONS.md`。201：`{"status": "success", "prototype": <原型对象>}`；400：`{"error", "reason": "invalid_prototype"}`（缺字段、父原型不存在或校验失败）
- `PATCH /admin/prototypes/{name}`、`PATCH /admin/prototypes/staging/{name}`——body `{display_name?, description?, behavior?, parent?, proposal_note?, lessons?}`（全空 → 400 `invalid_prototype`），behavior 变更重跑校验链（失败则磁盘原样）；给 `parent` 会重新挂谱系并重推 `generation`；`lessons` 重写 `LESSONS.md`、不传则保留。200：`{"status": "success", "prototype": <原型对象>}`；404：`prototype_not_found`
- `DELETE /admin/prototypes/{name}`——200：`{"status": "success"}`；404：`prototype_not_found`

### 演武场端点（docs/design/arena.md，WebUI PROTOTYPES staging 详情的演武场面板）

演武场由**人类专属触发**（与其他管理操作统一经过本地浏览器来源边界），run 在后台推进，证据归档在 staging 原型目录 `arena/<run-id>/`。facade 未装配时全部 503。

- `POST /admin/prototypes/staging/{name}/arena-runs`——触发一次 run（后台 task 推进）；body 全可选 `{model?, params?, corpus_size?, time_box_seconds?}`（`model` 留空走 `TLON_ARENA_MODEL`，两者皆无则拒绝）。202：`{"status": "success", "run": <ArenaRun.to_dict()>}`；400：`{"error", "reason": "arena_rejected"}`（staging 原型缺失/模型双缺/语料或时间盒超硬上限/已有活跃 run——v1 全局单并发）或 `"invalid_arena_request"`（字段类型错）
- `GET /admin/arena-runs`——全部 run 记录（最旧在前）。200：`{"status": "success", "runs": [<ArenaRun.to_dict()>]}`
- `GET /admin/arena-runs/{run_id}`——单个 run 实时状态（WebUI 轮询到终态 done/timed_out/failed）。200：`{"status": "success", "run": <ArenaRun.to_dict()>}`；404：`run_not_found`
- `GET /admin/prototypes/staging/{name}/arena-runs/{run_id}/report`——归档的 report.json + ARENA.md 全文。200：`{"status": "success", "report": <report.json 对象>, "arena_md": "<ARENA.md 全文>"}`；404：`report_not_found`（run 进行中或报告生成失败时同理）
- staging 详情（`GET /admin/prototypes/staging/{name}`）payload 另带 `arena` 字段：最新 run 目录有 report.json 时 `{"latest_run": <run.json 对象>, "red_flags": [<报告红旗 {kind, detail, evidence}>]}`，否则 `null`——与 execution-level 的 `red_flags` 是分开字段，不混。报告是证据不是判决，不进 approve 逻辑

### GET /traces（条件注册）

| Query 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 100（上限 1000） | 非整数回落默认值 |
| `source` | string | — | 按来源组件过滤 |

200：`{"traces": [<Trace.to_dict()>], "count": n}`；Trace 字段：`trace_id`、`root_span_id`、`name`、`start_time`、`source`、`spans`、可选 `end_time`/`duration_ms`/`metadata`（[base/tracing/trace_store.py](../../src/tlon/base/tracing/trace_store.py)）。

### GET /traces/{trace_id}（条件注册）

200：`{"trace": <Trace.to_dict()>, "span_tree": <嵌套树>}`。404：trace 不存在（`{"error"}`，无 reason）。

### GET /metrics

200：`MetricsService.snapshot()`（[base/metrics.py](../../src/tlon/base/metrics.py)），形状 `{"metrics": {name: {"type": "counter"|"histogram"|"gauge", "description", "unit", "points": [...]}}}`；counter/gauge 的 point 为 `{"attributes", "value"}`，histogram 的 point 为 `{"attributes", "count", "sum", "min", "max"}`。503：未装配 metrics 服务。

容器资源账本指标（issue #24，仅当装配了 `ContainerResourceMonitor` 时存在；observable gauge，collection 时从 monitor 的最新快照拉取）：

| 指标 | 类型 | attributes | 含义 |
|---|---|---|---|
| `tlon.container.memory_bytes` | gauge | `agent_id` | 成员容器实际内存占用（By） |
| `tlon.container.cpu_percent` | gauge | `agent_id` | 成员容器 CPU 占用（docker stats 公式，%） |
| `tlon.society.container_memory_bytes` | gauge | 无 | 全社容器内存占用总量（By，社会级暗账变明账） |
| `tlon.society.containers` | gauge | 无 | 运行中的成员容器数 |

### GET /logs

读 `logs_dir` 下**今天和昨天**的 `server-{date}.jsonl`（更早的文件不读），合并过滤后按时间序返回末尾 N 条。

| Query 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `trace_id` | string | — | 只看该 trace 的日志（trace → logs 串联） |
| `level` | string | — | 最低级别：`debug`/`info`/`warning`/`error`/`critical`；非法值不过滤 |
| `agent_id` | string | — | 只看该 agent |
| `before` | string | — | ISO 时间戳，只取早于它的条目 |
| `limit` | int | 100（上限 1000） | 取尾部 N 条 |

200：`{"logs": [<原始日志条目 dict>], "count": n}`。503：未装配 `logs_dir`。

### GET /agents/{agent_id}/logs

等价于 `GET /logs?agent_id=<id>`（支持 `limit`、`before`，不支持 `level`/`trace_id` 参数）。404：agent 未注册（先于 logs_dir 检查）；503：未装配 `logs_dir`。200：`{"agent_id", "logs", "count"}`。

### debug-only 端点

仅 `RESTAPIHandler(debug=True)` 时注册，用于冒烟，生产不开启：

- `GET /test/ping` → 200 `{"status": "ok", "pong": true, "timestamp"}`
- `POST /test/echo` → 200 `{"status": "ok", "echo": <请求体>, "timestamp"}`；非法 JSON 返回 400 `{"error"}`（无 reason）

## SSE：GET /events

处理函数 `handle_events`。一个会话一条流，服务端到客户端的唯一推送通道。

### 连接建立

- session id 走 query 参数 `session_id` 或请求头 `X-Session-Id`；**query 参数优先**。
- 会话不存在 → 404 `{"error"}`；该会话已有活跃流 → 409 `{"error", "reason": "stream_active"}`。多端登录是多个会话，不是一个会话多条流。
- 成功时 200，响应头 `Content-Type: text/event-stream`、`Cache-Control: no-cache`、`Connection: keep-alive`。
- 顺序保证：服务端先把帧队列登记进 `_sse_queues`（并发重复开流撞 409），再注册事件处理器、attach `HumanAdapter`，**然后**才 `prepare()` 响应——客户端看到 200 响应头时已保证能收事件。

### 事件帧格式

流上只有一种数据帧，由 `rest/sessions.py` 的 `_event_frame` 生成，一个 SSE `data:` 块一帧（`data: {json}\n\n`）：

```json
{
  "type": "event",
  "event_type": "mail.delivered",
  "payload": {"mail": {...}, "recipient": "human:guest"},
  "source": "mail_delivery",
  "timestamp": "<ISO>",
  "trace_id": "...", "span_id": "...", "parent_span_id": "..."
}
```

- payload 中带 `to_dict()` 的对象（如 `Mail`）序列化为平直 JSON。
- `trace_id` / `span_id` / `parent_span_id` 仅在事件携带时出现，用于把事件挂回服务端 trace 树。
- 心跳：每 `SSE_HEARTBEAT_SECONDS = 25` 秒无帧可发时写一行 `: ping` 注释（不是数据帧，SSE 解析器天然忽略）。**任何写失败（含心跳）即判定客户端离线**，走清理路径。

### 断开与重连

三种终结触发（客户端断连、心跳写失败、`DELETE /sessions/{id}`）走同一条清理路径：detach adapter、注销事件处理器、关闭会话。**SSE 断开 = 会话销毁**：服务端不支持断点续传，客户端重连必须重新 `POST /sessions` 拿新 session id 和新快照，再开新流。断连期间的瞬时事件（agent 状态变化等）丢失；邮件不丢（磁盘事实），重连后 `GET /inbox` 补齐。

帧队列是无界 `asyncio.Queue`：慢客户端只积压自己的内存，不反压事件源；实际上极慢的客户端会先被心跳写失败判定离线。

### 推送双轨与事件清单

下行推送有两条不重叠的路径，任何一个会话的任何一个事件只从一个口子进来：

1. **chat 会话的 `mail.delivered`**：经 [organization/messaging/human_adapter.py](../../src/tlon/organization/messaging/human_adapter.py) 的 `HumanAdapter`——它订阅 `MAIL_DELIVERED`，把 `payload.recipient == 自己 member_id` 的副本 fan-out 给 attach 的会话（成员视角：你的邮箱到了一封信）。
2. **其余事件**：经 [tlon_server/core/notification_service.py](../../src/tlon_server/core/notification_service.py) 的 `NotificationService`，订阅全部 `EventType`，按会话 mode + filters 逐个过滤后推送（观察者视角）。

事件类型枚举在 [base/events/event.py](../../src/tlon/base/events/event.py) 的 `EventType`：

<!-- BEGIN GENERATED: event-types -->
枚举全集（31 个，生成自源码，注释即源码行内注释）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| event_type | 枚举成员 | 说明 |
|---|---|---|
| `mail.new` | `MAIL_NEW` | A mail was committed by the post office (notification only) |
| `mail.delivered` | `MAIL_DELIVERED` | A mail landed in one recipient's inbox |
| `mail.endorsed` | `MAIL_ENDORSED` | A delivered mail endorses another mail (reputation signal, see docs/design/mail.md) |
| `agent.join` | `AGENT_JOIN` |  |
| `agent.leave` | `AGENT_LEAVE` |  |
| `agent.response` | `AGENT_RESPONSE` |  |
| `agent.started` | `AGENT_STARTED` |  |
| `agent.stopped` | `AGENT_STOPPED` |  |
| `agent.retired` | `AGENT_RETIRED` | Offline handover inventory after permanent exit |
| `agent.error` | `AGENT_ERROR` |  |
| `agent.state_changed` | `AGENT_STATE_CHANGED` |  |
| `agent.waiting` | `AGENT_WAITING` | Waiting remains waiting when its runtime is unloaded |
| `agent.waking` | `AGENT_WAKING` |  |
| `agent.woken` | `AGENT_WOKEN` |  |
| `agent.wake_failed` | `AGENT_WAKE_FAILED` |  |
| `agent.sleeping` | `AGENT_SLEEPING` |  |
| `agent.start_request` | `AGENT_START_REQUEST` |  |
| `agent.stop_request` | `AGENT_STOP_REQUEST` |  |
| `agent.idle` | `AGENT_IDLE` |  |
| `agent.spawned` | `AGENT_SPAWNED` |  |
| `agent.noisy` | `AGENT_NOISY` |  |
| `mail.orphan_scan` | `MAIL_ORPHAN_SCAN` |  |
| `agent.llm_request` | `AGENT_LLM_REQUEST` |  |
| `agent.llm_response` | `AGENT_LLM_RESPONSE` |  |
| `tool.error` | `TOOL_ERROR` |  |
| `tool.execute_start` | `TOOL_EXECUTE_START` |  |
| `tool.execute_end` | `TOOL_EXECUTE_END` |  |
| `system.startup` | `SYSTEM_STARTUP` |  |
| `system.shutdown` | `SYSTEM_SHUTDOWN` |  |
| `system.error` | `SYSTEM_ERROR` |  |
| `config.updated` | `CONFIG_UPDATED` |  |
<!-- END GENERATED: event-types -->

各事件的产生者与 payload（手写维护，按域分组）：

| event_type | 产生者 | payload 主要字段 |
|---|---|---|
| `mail.new` | `PostOffice`（source 为发件人成员 id 或外部地址） | Mail 对象（本地事务已提交，仅通知，不驱动投递） |
| `mail.delivered` | `PostOffice`（source `post_office`，本地入箱及网关信箱成功尝试后通知；不表示已读或外部平台收到） | `mail`（Mail）、`recipient`（成员 id） |
| `agent.started` / `agent.stopped` | `AgentRegistry` | `agent_id`、`name`、`runtime`、`spec_hash` / `agent_id`、`stopped_by` |
| `agent.retired` | `RetirementCeremony`（source `retirement_ceremony`） | `agent_id`、`name`、`stopped_by`、`open_tasks`（标题数组）、`open_threads`（thread_id/subject/from 对象数组）、`memory_files`（workspace 相对笔记路径，不读取正文） |
| `agent.waking` / `agent.woken` / `agent.wake_failed` / `agent.sleeping` | `AgentRegistry` | `agent_id` 等（woken 带 `wake_duration_seconds`） |
| `agent.start_request` / `agent.stop_request` / `agent.idle` | Adam / `AgentRegistry` | `agent_id`、`reason` / `agent_id`、`idle_seconds` |
| `agent.state_changed` | `AgentRegistry.publish_scheduling_state` | `get_agent_status` 返回的权威成员快照（含 id/state/wake_at 等） |
| `agent.spawned` | `AgentRegistry` | `agent_id`、`name`、`prototype`、`spawner_id`、`spawn_depth`、`started` |
| `system.startup` / `system.shutdown` | `ServerApp`（source `server`） | 如 `{"agents": n}` |
| `config.updated` | 配置层（`ConfigManager.reload_agent`） | `{"agent_id", "action"}` |
| `agent.llm_request` / `agent.llm_response` | `ModelService.request` | `agent_id`、`model`；请求含数量，完成含耗时、finish_reason 与 token 用量 |
| `tool.execute_start` / `tool.execute_end` / `tool.error` | `OperationExecutor` 的实际工具 handler 调用 | `agent_id`、`tool_name`；结束含 success，错误含 error |

模型调用由 `ModelService` 发布 `agent.llm_request` / `agent.llm_response`，包含成员与模型身份、消息/工具数量、耗时和 token 用量，不携带消息正文。工具由 `OperationExecutor` 在 `origin=tool` 的实际 handler 调用处发布 `tool.execute_start`、`tool.execute_end` / `tool.error`；普通程序直接调用 Operation 不冒充工具。回放持久回执和恢复已提交结果均不重新发布或累计指标。事件是尽力观测，取消、超时或进程退出可能只有 start，不能据此推断效果未发生；持久执行证据仍读取 ProgramStore/交互历史。

旧 stage 的 `agent.thinking` / `agent.thinking_start` / `agent.thinking_end` 以及无发布者的 `tool.execute` / `tool.result` 已删除。以下事件名仍保留但当前无生产发布点：`agent.join`、`agent.leave`、`agent.response`、`agent.error`、`system.error`。

### mode 与 filters

过滤管线在 `NotificationService._should_send_to_session`，各步按 AND 组合：

0. **单轨排除**：chat 模式的会话**永不**经 NotificationService 收到 `mail.delivered`（那封邮件正从 HumanAdapter 过来）；event/agent 模式豁免（运维控制台要看全量邮件流量）。
1. **mode 语义**（`MODE_EVENT_PATTERNS`，唯一权威）：

<!-- BEGIN GENERATED: mode-patterns -->
生成自 `MODE_EVENT_PATTERNS`（`.*` 后缀为前缀匹配，其余精确匹配；表外的 mode —— 即 `event` —— 不加限制）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| mode | 事件模式 |
|---|---|
| `chat` | `mail.*`、`agent.response`、`agent.started`、`agent.stopped`、`agent.retired`、`agent.state_changed`、`agent.sleeping`、`agent.error`、`system.*` |
| `agent` | `agent.*` |
<!-- END GENERATED: mode-patterns -->

2. **filters**（会话级，全部可选）：

| filter 键 | 类型 | 语义 |
|---|---|---|
| `trace_id` | string | 只看携带该 trace id 的事件（取事件 envelope，否则 payload 里的 `trace_id`） |
| `event_types` | list[string] | 事件类型包含列表，支持 `*` 与 `prefix.*` |
| `exclude_event_types` | list[string] | 事件类型排除列表，同上 |
| `agent` | string | 按事件 source 过滤，支持 `*` 通配（`prefix-*` 前缀匹配） |

mode 与 filters 可经 `PATCH /sessions/{id}` 在线修改。过滤只在服务端执行，客户端本地不再二次过滤。

## 接入示例

以下示例用于本机 HTTP 检查；当前维护的客户端是 WebUI，不保证旧 SDK、TUI 或 Telegram 兼容。

### curl

```bash
BASE=http://127.0.0.1:8080

# 1. 查看实例身份；仅未设置时执行首次设置
curl -s "$BASE/identity" -H 'X-Tlon-WebUI: 1'
curl -s -X POST "$BASE/identity" -H 'X-Tlon-WebUI: 1' \
  -H 'Content-Type: application/json' -d '{"username":"alice"}'

# 2. 建会话，使用返回的 session_id（不提交 username）
curl -s -X POST "$BASE/sessions" -H 'X-Tlon-WebUI: 1' \
  -H 'Content-Type: application/json' -d '{
  "client_type": "webui", "mode": "chat", "protocol_version": "2.0.0"
}'

# 3. 另开终端读 SSE（此处示例 session_id，实际以建会话响应为准）
curl -N "$BASE/events?session_id=webui-0001" -H 'X-Tlon-WebUI: 1'

# 4. 发信；202 表示本地入箱，不表示阅读或网关外部投递成功
curl -s -X POST "$BASE/messages" -H 'X-Tlon-WebUI: 1' \
  -H 'Content-Type: application/json' -d '{
  "to": ["dev-001"], "request_id": "example-001", "content": "你好"
}'

# 5. 拉历史、显式标读与清理会话
curl -s "$BASE/inbox?limit=20" -H 'X-Tlon-WebUI: 1'
curl -s -X POST "$BASE/inbox/read" -H 'X-Tlon-WebUI: 1' \
  -H 'Content-Type: application/json' -d '{"read":true,"thread_id":"<线程 id>"}'
curl -s -X DELETE "$BASE/sessions/webui-0001" -H 'X-Tlon-WebUI: 1'
```

### WebUI 同源请求

页面由 dream 的 `/ui/` 提供，开发时经 Vite 同源代理。fetch 一律带固定来源边界头；原生 EventSource 由浏览器发送 `Sec-Fetch-Site: same-origin`。

```javascript
const response = await fetch("/identity", {
  headers: { "X-Tlon-WebUI": "1" },
});
if (!response.ok) throw new Error(`Identity request failed: ${response.status}`);
const identity = await response.json();
// username 为 null 时显示首次设置表单，成功后再建立 session。

// 建会话并切换 event mode 后，用真实返回的 session_id 开流。
const source = new EventSource(`/events?session_id=${encodeURIComponent(sessionId)}`);
```

断流后应重新 POST `/sessions`、切换模式并打开新流，恢复时以 `/inbox` 持久快照补齐邮件；EventSource 重连不能复用已经销毁的会话。实现见 [WebUI REST 客户端](../../apps/webui/src/client/rest.ts) 与 [SSE 客户端](../../apps/webui/src/client/sse.ts)。

### 系统分配的技术 ID（ADR 0035）

POST /admin/config/agents 的请求为 `{prototype, name, model, params?, persona?}`，其中 prototype 是原型 ID、name 是成员显示名；agent_id 由系统生成 m-UUID，携带 agent_id（包括空值）返回 400 `managed_id`，不留下新成员。响应的 agent_id 用于收信、详情和管理。POST /admin/prototypes 不接受自报 name，返回的 prototype.name 是系统分配的 p-UUID。原型 display_name 创建必填、编辑不可清空；名字和描述应表达行为，不授予成员职业。已有用户文件不迁移或重编号，无旧职业名映射。

### 成员人格文件管理

`GET /admin/config/agents/{agent_id}` 返回 `{status, agent_id, persona: {identity, profile, behavior}, llm: {model_name}}`；三个 persona 字段分别是 IDENTITY.md、SOUL.md、RULES.md 的全文，缺失文件返回空字符串，未知成员返回 404。仅管理面提供此接口，不改变成员之间的人格私有边界。

创建和 PATCH 使用相同的 persona 字段名。IDENTITY 定义自我叙述，SOUL 定义价值观、性格、表达与判断倾向，RULES 定义工作规则与流程。PATCH 中省略字段表示不改，空字符串表示清空；persona 必须是仅包含上述字段的字符串对象，无效输入返回 400。

### 原型模块包（P1）

`POST /admin/prototypes` 和新增 `POST /admin/prototypes/staging` 接受可选 `modules: {相对路径: 源码字符串}`；v2 必须提供完整依赖，v1 不允许附带非空模块。staging 使用同一人工管理来源边界，后续经既有 approve 入口批准。详情 GET 附加 modules 全量源码；实例化复制完整包并校验已批准内容摘要。v2 behavior/modules PATCH 返回 400，源码变更必须创建新原型；元数据更新不重新批准源码。细节见[指南](../guides/writing-a-module-program.md)。
