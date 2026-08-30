# ADR 0013：admin 端点共享 token 鉴权（TLON_ADMIN_TOKEN）

- 状态：已接受（2026-08-30，方向讨论拍板）
- 相关：issue （安全面为零）；[design/server-protocol.md](../design/server-protocol.md)「身份与安全边界」

## 背景

Tlon REST 面零鉴权（issue ）：包括 `POST/DELETE /admin/config/agents`（创建/删除成员）、`PUT /admin/settings/env`（改写 `{TLON_HOME}/.env`，含 LLM key）在内的全部 admin 端点对任何能到达端口的人开放。默认绑定 `127.0.0.1` 时信任边界是本机，可接受；但一旦绑定非回环地址（或经端口转发暴露），任何人可读写全部数据并触发热加载。

非回环绑定的启动警告已在前置 PR 落地（banner + `server_exposed_non_loopback` 日志），剩下的实质防线是认证本身。

## 决策

引入**最小共享 token 认证**，只保护 admin 面：

- 新增环境变量 `TLON_ADMIN_TOKEN`（`base/settings.py`，从 `{TLON_HOME}/.env` 或真实环境读取）。**为空（默认）= 认证关闭，行为与现状完全一致**——本机单用户工具保持零摩擦。
- 配置后，所有 `/admin/*` 请求必须携带 `Authorization: Bearer <token>`，否则 401 `{"error", "reason": "unauthorized"}`（复用统一错误形态）。比较用 `hmac.compare_digest`（防时序侧信道）。
- 强制点在 `RESTAPIHandler.create_app` 注册的 aiohttp middleware——单一拦截点，未来新增 admin 路由自动被保护，不依赖每个 handler 自觉。CORS 预检（OPTIONS）不携带凭证，按规范放行；`Access-Control-Allow-Headers` 增加 `Authorization`。
- **非 admin 端点不保护**。邮件面（`/messages`、`/inbox`、`/sessions`、`/events`）的身份模型是「名字即身份」（见 server-protocol.md），加 token 不改变这一语义，只会给四个客户端增加协议负担。需要保护邮件面的场景应绑定回环地址。
- 客户端（SDK / ops TUI / WebUI / Telegram）本期不改：默认关闭时无影响；启用 token 的用户以自带 HTTP 客户端（curl 等）调用 admin 面。客户端原生支持 token 是后续独立工作。

### 为什么不做更完整方案

- **全端点 token**：把本地工具变成每次交互都要带凭证，与「本机单用户」的核心场景冲突，且要同步改四个客户端。
- **多用户 / RBAC**：Tlon 成员模型没有「用户权限」概念，预防性抽象（项目规范 2）。
- **mTLS / IP 白名单**：运维成本高，超出当前需求。

共享 token 是「能满足当前需求的最简单实现」：一个环境变量、一个 middleware，把 issue  最危险的面（admin 写操作 + `.env` 读写）守住。

## 后果

- 默认行为不变：未配置 `TLON_ADMIN_TOKEN` 时所有端点保持开放，现有客户端、集成测试全部不受影响。
- 配置后 WebUI 的 admin 功能（Agents/Settings 视图）会因 401 不可用——这是本期的已知取舍，已在上文声明。
- token 与 LLM key 同级存放（`{TLON_HOME}/.env`），泄露面相同；`.env` 文件权限与 TLON_HOME 保护不变。
