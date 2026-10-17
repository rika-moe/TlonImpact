# 服务端协议设计（REST + SSE，协议 v2）

Tlon 服务端对外只暴露一个 HTTP 端口：纯 REST + SSE，没有 WebSocket。本文解释这个协议面为什么长这样，以当前实现为准（[src/tlon_server/api/rest/](../../src/tlon_server/api/rest) 包的 `RESTServer` / `RESTAPIHandler`）。选 SSE 的决策记录见 [docs/decisions/0005-sse-over-websocket.md](../decisions/0005-sse-over-websocket.md)；协议所服务的邮件与成员模型见 [docs/design/mail.md](./mail.md)。

## 形态：一个端口，两种动词

协议 v2（`SESSION_PROTOCOL_VERSION = "2.0.0"`，`rest/sessions.py` 中 `POST /sessions` 唯一接受的版本）只有两种数据流向：

- **客户端 → 服务端：普通 HTTP 请求**。发信、建会话、改过滤、查状态，全使用 REST 请求。
- **服务端 → 客户端：一条 SSE 长连接**。`GET /events` 返回 `text/event-stream`，事件帧是 JSON（`{"type": "event", "event_type", "payload", "source", "timestamp", ...}`，含 trace 字段），一个 SSE `data:` 块一帧；每 `SSE_HEARTBEAT_SECONDS = 25` 秒发一行 `: ping` 注释作为心跳——写心跳失败即判定客户端已走。

版本协商只有一个检查点：建立会话时客户端声明 `protocol_version`，不等于当前版本即拒绝（`unsupported_protocol_version`，响应带支持版本列表）。会话建立后整个生命周期内协议不变——没有逐帧版本号、没有能力协商位图。这是「协议面小」原则在演进策略上的延伸：版本升级就是换掉整个会话语义，而不是让新旧帧格式长期共存（项目规范：不保留向后兼容）。

没有请求/响应配对的 RPC-over-socket，没有服务端向客户端发起的「调用」。上行用 POST，下行用 SSE，各走各的。

这个二分直接来自系统的通信拓扑：人类客户端对服务端只有「发起动作」和「接收通知」两种关系，而收件人的阅读与处理是异步的——投信请求同步完成本地入箱，事件推送不需要客户端应答。把两个方向拆到两条通道上，每条通道各自是 HTTP 里最朴素的形态；合起来却覆盖了全部需求。

## 事件帧格式

SSE 流上只有一种数据帧（`rest/sessions.py` 的 `_event_frame` 生成）：

```json
{"type": "event", "event_type": "mail.delivered", "payload": {...}, "source": "mail_delivery", "timestamp": "...", "trace_id": "...", "span_id": "...", "parent_span_id": "..."}
```

payload 里的 `Mail` 等对象序列化为 `to_dict()` 的平直 JSON；trace 三字段在事件携带时透传，让客户端能把事件挂回服务端 trace 树做关联观察（SDK 和 TUI 的 trace 过滤就靠它）。心跳是 SSE 注释行（`: ping`），不是数据帧——客户端解析器天然忽略注释，不需要专门处理。帧格式没有版本字段：协议版本在会话建立时握手（`protocol_version`），会话存续期间帧格式不变。

## 端点分组

路由集中在 `RESTAPIHandler.create_app` 注册。端点全表（每条路由的注册条件、请求/响应细节、错误枚举）以 [reference/api-reference.md](../reference/api-reference.md) 的生成总表为单一事实源，本文不手工复制清单（双写必漂移）。按职责粗分：消息（`POST /messages` 发信、`GET /inbox` 分页读邮箱、`POST /inbox/read` 显式标读）、会话（`POST/PATCH/DELETE /sessions` + `GET /events` SSE 流）、成员与配置（含 `/admin/*` 管理面）、可观测性（traces/metrics/logs）。

几个端点层面的设计选择：

- **错误形态统一**：所有 4xx 都返回 `{"error", "reason"}`，`reason` 是机器可读的稳定枚举（`invalid_json`、`invalid_username`、`unsupported_protocol_version`、`recipient_unresolved` 等），`error` 是给人看的消息。客户端分支判 `reason`，不解析文案。
- **快照随会话建立返回**：`POST /sessions` 的 201 响应内嵌 participants/agents 快照，省掉客户端启动时的一轮级联查询；之后的变化靠 SSE 事件增量。这是「历史 + 增量」读模型在成员目录上的复用。
- **读写职责明确**：邮件写操作包括 `POST /messages` 投信与 `POST /inbox/read` 显式修改人类收件关系的已读状态；会话管理使用 POST/PATCH/DELETE，管理面另有配置写入接口。读取邮箱和观察成员不改变已读状态。
- **条件注册**：admin 配置路由在 configs facade 注入时才注册，traces 路由在 observability facade 注入时才注册，`/metrics` 无观测 facade 时返回 503。装配层（`ServerApp`）决定哪些能力上线，API 层不猜测——缺失的能力以明确的 503/缺路由表达，不伪装成功。
- **热重载走管理面**：`POST /admin/config/agents/{agent_id}/reload` 让运行中的服务端注册并启动一个磁盘上已存在的 agent（或登记为 lazy）——创建类管理操作的收尾。配置的唯一权威仍是磁盘文件，端点只是触发重读。

会话即存在（presence）：SSE 流建立时才把会话 attach 到对应人类的 `HumanAdapter` 并注册事件处理器；流断开（客户端断连、心跳写失败、主动 DELETE）即可靠地 detach 并关闭会话——**会话 SSE 断开即结束该 attachment**，客户端重连需重新 `POST /sessions` 拿新 session id 和新快照。

## 会话生命周期

一个会话从生到灭的完整路径，以及几个刻意的顺序安排：

1. **建立**：`POST /sessions` 只创建会话记录（身份、mode、filters），返回 session id 和 participants/agents 快照。此时没有事件处理器、没有 adapter attach——客户端还没开流，推了也没人收。
2. **开流**：`GET /events` 带着 session id 到来时才接线：`handle_events` 先把该会话的帧队列登记进 `_sse_queues`（在第一个 await 之前，让并发的重复开流撞上 409 而不是竞争），再注册事件处理器、attach HumanAdapter，**然后**才 `prepare()` 响应——客户端看到 200 响应头时，服务端保证它已经能收到事件。这个「先接线后放行」的顺序消掉了「响应到了但事件还在丢」的窗口。开流时注册的事件处理器和 attach 给 adapter 的推送回调是**同一个闭包**：事件经它入帧队列，发给这个人类的邮件也经它入帧队列——单轨投递在协议层就体现为「一个会话一个帧队列」，不存在第二条到客户端的路。
3. **存续**：事件处理器把帧写入队列，循环消费写出；队列里放 `None` 哨兵是流结束的内部信号（DELETE 路径用）。同一会话只允许一条活跃流（409 `stream_active`）——多端登录是多个会话 attach 同一个 adapter，不是一个会话多条流。帧队列是无界 `asyncio.Queue`：慢客户端的积压只涨自己队列的内存，不反压事件源；这与 EventBus 的有界订阅队列取向不同，取舍是「会话是短命凭据，客户端跟不上时断流重建比丢事件更直白」之外的另一层——实际上极慢的客户端会先被心跳写失败判定为离线。
4. **终结**：三种触发（客户端断连、心跳写失败、`DELETE /sessions/{id}`）走同一条清理路径：detach adapter、注销处理器、关闭会话。DELETE 是幂等的，清理步骤重复执行无害。服务端不尝试区分「网络抖动」和「客户端走了」——心跳写失败即离线，不承诺靠心跳为极慢客户端提供确定的积压上限。

会话状态全部在内存（`SessionManager`），服务端重启即清空——邮件仍在持久邮箱，但断线期间瞬时事件不会重放；客户端重连后重新建会话并同步持久状态。会话是廉价的、可随时重建的「在线凭据」，不是需要保护的资产。

## 邮箱快照、实时通知与分页

SSE 不提供持久事件游标，也不以 `Last-Event-ID` 补播邮件。邮件完整性由 SQLite 快照承担；客户端必须先注册事件监听，等 SSE 实际开流就绪后再读快照，不能把 `POST /sessions` 成功当成流已就绪。

`GET /inbox` 与观察成员邮箱接口返回 inbox + sent 的合并页，按数据库 timestamp/rowid 稳定排序。`before` 表示上一页最早邮件 id，`has_more` 与 `next_before` 指示是否还有更早历史；它们是邮箱历史游标，不是 SSE 断点。观察接口不暴露他人 read。

初次同步可以只取最近一页并明确还有历史。重连补信应从最新页向前分页，直到覆盖上一次**完整成功快照**的最新邮件边界或到达历史末尾，不能只取固定条数，也不能把断线期间本地发出的新信当作补信边界。加载期间实时邮件与快照按 id 合并；快照能更新同 id 的已读状态。具体页面竞态控制见 [webui.md](./webui.md)。

`POST /inbox/read` 只修改指定人类成员的收件关系，目标为单封或完整线程；线程更新在一个事务中包含尚未加载的历史，返回实际受影响邮件 id。查看、打开线程、重连均不自动标读，观察他人保持只读。精确字段和错误见 [API reference](../reference/api-reference.md)，邮局语义见 [mail.md](./mail.md)。

## 推送分工：单轨邮件 + 通知服务

下行推送有两条路径，刻意不重叠：

- **chat 类会话的邮件**走成员层：`HumanAdapter` 订阅 `MAIL_DELIVERED`，把发给自己成员 id 的副本 fan-out 给 attach 的会话。人类收信和 agent 收信遵循同一条「每成员一个邮箱、经成员投递」的规则。
- **系统/agent 事件**走 [tlon_server/core/notification_service.py](../../src/tlon_server/core/notification_service.py) 的 `NotificationService`：订阅全部事件类型，按会话的 mode 语义（`MODE_EVENT_PATTERNS`：chat 收 `mail.*`/`agent.response`/`agent.started` 等、`agent` 收 `agent.*`、`event` 不加限制）和会话 filters（`trace_id`、`event_types`、`exclude_event_types`、`agent`，支持 `prefix.*` / `*` 通配）逐个会话过滤后推送。

### mode 与 filters

mode 是会话级的粗粒度语义，filters 是其上的细粒度裁剪，过滤管线按 AND 组合：

1. chat 会话的 `mail.delivered` 排除规则（见下文，第 0 步）；
2. mode 语义：`MODE_EVENT_PATTERNS` 是 mode 到事件类型模式表的唯一权威——chat 收邮件类与 agent 生命周期/响应事件，`agent` 收 `agent.*`，未列出的 mode（`event`）不加限制；
3. filters 逐项裁剪：`trace_id` 只看某条 trace 的事件，`event_types` / `exclude_event_types` 是事件类型的包含/排除列表，`agent` 按事件来源过滤（通配 `*`）。

mode 与 filters 都可经 `PATCH /sessions/{id}` 在线修改，立即生效，无需重开流。

避免双轨的显式规则在 `NotificationService._should_send_to_session` 第 0 步：chat 模式的会话**永不**经 NotificationService 收到 `mail.delivered`——那封邮件正从它的 HumanAdapter 过来。event/agent 模式（运维控制台）豁免，因为它们要观察全量邮件流量。

为什么不让 NotificationService 统一推一切、再去重？因为两条路径的语义不同：HumanAdapter 推的是「你的邮箱到了一封信」（成员视角，已按收件人过滤，只含发给自己的副本），NotificationService 推的是「系统里发生了一个事件」（观察者视角，按 mode/filters 过滤）。chat 会话要的是前者——一个人类用户打开邮箱客户端，想看自己的信，不是全社会的邮件流量；运维控制台要的是后者。让两类语义各有其推送点，再在 NotificationService 里显式排除重叠，比统一后按 mode 特判要直白。双轨的代价（同一件事件可能有两条推送路径）被限定为**零重叠**：排除规则保证任何会话的任何一个事件只从一个口子进来。

过滤只在服务端做。客户端（SDK adapter）只声明自己想要的 mode 和 filters，随会话建立/PATCH 发给服务端，本地不再二次过滤——过滤规则只有一个权威副本。

## 身份与安全边界

协议没有认证层：`POST /messages` 的发件人身份来自请求体的 `username`，`GET /inbox` 的身份来自 query 参数，`POST /sessions` 同理。这是刻意的——服务端默认绑定 `127.0.0.1:8080`，信任边界是本机。这不是「尚未实现认证」，而是和邮件系统的身份模型对齐：书信共和国的成员按名字存在，名字即身份；在本机单用户场景里，声明 `username` 就是出示身份。

唯一的输入防御是路径安全：username 是人类身份输入，也涉及本地路径安全，因此 `rest/common.py` 的 `_validate_username` 拒绝空值和任何携带路径分隔符的输入（traversal guard）。CORS 全开（`Access-Control-Allow-Origin: *`），同样是本地开发工具的取向。debug 模式的 `/test/ping`、`/test/echo` 只用于冒烟，生产永不开启。

绑定非回环地址（`--host 0.0.0.0` 或任何局域网/公网地址）时，服务端不阻止——显式给出 `--host` 本身就是知情动作——但会在两处留下醒目记录：启动 banner 追加「NO authentication」警告块，日志打 `server_exposed_non_loopback` warning（判定逻辑是 [tlon_server/serverapp.py](../../src/tlon_server/serverapp.py) 的 `_LOOPBACK_HOSTS` 白名单：`127.0.0.1` / `localhost` / `::1`）。

唯一的认证层是 admin 面的共享 token（ADR 0013）：配置 `TLON_ADMIN_TOKEN` 后，所有 `/admin/*` 请求必须携带 `Authorization: Bearer <token>`，否则 401（`reason: unauthorized`）；强制点是 `RESTAPIHandler.create_app` 注册的 middleware，单一拦截、未来新增 admin 路由自动被保护。默认（未配置）认证关闭，行为与上述「名字即身份」模型完全一致——它是给非回环暴露场景的可选硬防线，不是本地工具的额外摩擦。邮件面（`/messages`、`/inbox`、`/sessions`、`/events`）不在保护范围内：需要保护它们时应绑定回环地址。

## 客户端

官方客户端都只经 REST + SSE 与服务端通信：

- [apps/operator](../../apps/operator)（TypeScript/Ink TUI）：`src/client/sseClient.ts` 的 `SseClient` 用 fetch 实现整个协议——`POST /sessions` 开流前拿快照，`GET /events` 读流，PATCH 改 mode，断线后指数退避重连（`backoffDelay`，1s 起步、30s 封顶）并**从零重建会话**（新 session id、新快照），断连期间的事件丢弃。`dispose` 中止流并尽力 DELETE 会话。整个客户端协议实现只有这一个文件、两百余行——这是协议面小的直接证据。
- [WebUI](./webui.md)：浏览器 EventSource + REST，真实开流后报告 connected；邮箱另以持久快照同步。
- [Telegram](./telegram.md)：经 Python SDK 的 chat 会话接入，仅收自己身份的邮件。
- [src/tlon_client/sdk/](../../src/tlon_client/sdk)（Python SDK）：`client.py` 的 `TlonClient` = `base_client.py` 的 `BaseClient`（REST + SSE 传输、自动重连、重连后重新 `POST /sessions`）+ `adapter.py` 的 `ClientAdapter`（按客户端类型声明 client_type/mode/filters 等默认行为）。契约上不依赖 `tlon` 包（import-linter 强制）——SDK 能用的就是协议暴露的，没有后门。

客户端共同的约定值得强调：**过滤声明在服务端执行**。adapter 把想要的 mode 和 filters 随 `POST /sessions` / `PATCH` 发给服务端，本地收到什么就展示什么，不再二次过滤。这让「客户端看到什么」的语义只有一个权威实现（`NotificationService`），客户端类型再怎么增加也不会各自漂移出自己的过滤规则。

## 设计取舍

**为什么无状态 REST + 单轨 SSE 够用**：

- 人类客户端的读模型是「历史 + 增量」：历史用 `GET /inbox` 直接拉持久邮箱（邮箱本来就是磁盘上可重读的事实），增量靠 SSE。两条都是 HTTP 原语，不需要会话级协议状态。重连语义因此极其简单：断了就重新建会话、打开 SSE 流，流就绪后分页补拉 inbox，直到覆盖断线前的最新邮件；加载期间的事件与快照按邮件 id 合并。邮箱游标用于历史分页，不是 SSE 事件断点。
- `POST /messages` 成功回执列出本地已入箱成员、网关尝试结果与请求 ID。本地事务已提交，阅读与处理仍是异步的；网关只承诺尝试，不保证网关入箱或外部接收。任一地址无效或成员退休整封拒绝，返回结构化问题列表，不另发退信。超时是结果未知，重试复用 `request_id`；同键同内容返回既有结果，同键不同内容冲突，不重复入箱或尝试网关。
- agent 不需要这个协议：它们活在 runtime 中，成员间通信经邮局，EventBus 传递进程内通知。协议面只需服务人类客户端，单边推送 + 普通 POST 恰好覆盖，全双工是无谓的复杂度。
- 协议面小的好处是可接入性：任何能发 HTTP 请求、能读 SSE 的客户端（curl、脚本、第三方 TUI）都是一等公民，不需要专用 SDK 或 WebSocket 库。

这套协议本质上是邮件系统的「人类终端适配层」：它不发明自己的消息语义，只把邮箱（`GET /inbox`）、发言（`POST /messages`）、成员在场（sessions + adapter attach）和总线观察（SSE 事件流）翻译成 HTTP。协议也承载管理与观测；各端点应明确对应领域动作，不在 HTTP 层重新发明领域语义。

**代价与边界**：

- SSE 在 HTTP/1.1 下每连接占一个浏览器连接槽——本场景是本地单用户 TUI/SDK，无感；若未来出现浏览器多标签客户端，再评估 HTTP/2。
- 推送通道不能上行——上行本来就该走 POST，职责分离反而清晰。
- 断连期间的非邮件事件（agent 状态变化等瞬时通知）会丢，客户端以重连后的会话快照为新的真相起点；邮件不丢，因为邮件是磁盘事实。
- 单条 SSE 流绑定单个会话，多端是同用户的多个会话；没有广播级的服务端推送聚合，会话数即推送扇出数——本地单用户场景下这不是约束。

## 端口与生命周期

`RESTServer` 是 aiohttp 生命周期的薄封装：`start()` 建 app、起 `AppRunner` 和 `TCPSite`，`stop()` 清理。装配在 `ServerApp.initialize()` 完成（构造 `RESTAPIHandler` 并注入 ClientGateway 与 [tlon/api/](../../src/tlon/api) 的 domain facade——members / mail / prototypes / observability / configs / events / arena / capabilities，ADR 0019；handler 不直摸 runtime 内脏），但**装配阶段不开端口**——监听发生在 `ServerApp.start()` 调 `RESTServer.start()` 时；关停严格反序，RESTServer 最先停。这条顺序保证两件事：装配失败时进程没有任何对外暴露；关停时不再接受新请求之后，内部组件才开始拆。

默认绑定 `127.0.0.1:8080`，对外只有这一个端口。可观测性（traces/metrics/logs）也走同一个端口的 GET 端点，不开独立的 admin 端口——本地工具场景下，第二个端口是比「端点级的条件注册」更重的隔离手段。

至此协议面完整：一个端口、一组无状态 REST 端点、一条 SSE 流、一个版本号。它的每一处设计都能回溯到同一个根源——邮件是唯一通道，协议只是让人类也能站进这条通道里。
