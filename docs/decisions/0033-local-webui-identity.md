# ADR 0033：本地单人 WebUI 身份与浏览器来源边界

> 后续变更（2026-10-31）：Operator TUI、Python SDK 与 Telegram 已[退役删除](legacy-client-retirement.md)；下文涉及它们的实现和验证记录属于历史。当前客户端为 WebUI，包结构与入口以[当前架构](../architecture.md)为准。

- 状态：已采纳（2026-10-17）
- 关联：#144 跨域读信／冒名发信；替代 ADR 0013 的 admin token 方案

## 决策

Tlon 作为本机单人应用运行，不建立账号密码、认证 Cookie、JWT、开放注册或角色系统。首次打开 WebUI 设置一个 username，随后自动进入。username 是实例持久数据，保存在 `~/.tlon/webui-identity.json`，通过入口构造的 TlonPaths 显式注入；换浏览器、重启服务不改变身份。只允许初始化一次，不提供改地址功能。旧 guest 邮箱与关系原样保留，不迁移、不关联。

username 使用 ASCII 地址 local-part 子集（字母数字开头，后续字母数字、点、下划线、连字符，最长 64）；拒绝系统保留地址与已有 agent id，避免寻址冲突。地址为 `<username>@tlon.internal`，内部适配仍用 `human:<username>`；agents 只看到普通 member 邮件，不增加人类、管理员或认证标识。

地址不冲突是整个实例生命周期的不变式：创建、reload 和 PATCH 成员均须在任何配置／人格写入及 adopt 前检查；冷启动加载持久身份后须比对磁盘启动成员，在 HTTP 开始服务前拒绝冲突，并清理已装配资源。不能只在首次设置身份时检查。

`GET /identity` 返回 `{username: string|null, address: string|null}`；`POST /identity` 接受 `{username}`，首次成功 201，重复初始化 409，非法名字 400。未初始化时仅允许静态 WebUI、最小 `/health` 与 identity API，其余业务 API 返回 409 `identity_required`。初始化失败不得写入身份；损坏的持久文件必须显式报错，不能回落为 guest。

发信、自己的邮箱、已读修改与 SSE session 的人类身份一律由服务端实例身份决定。删除这些 API 的客户端 username 参数（带旧字段返回 400 `identity_managed`），不保留自报身份入口。观察其他成员邮箱仍是本机操作者的管理能力，不会将其作为发件人。

## #144：来源边界

取消认证不等于允许任意网页操作本地服务。服务端仅绑定 `127.0.0.1`、`localhost` 或 `::1`；非回环绑定直接拒绝，不再只警告。校验请求 Host 为实际监听端口上的回环 authority，拒绝 DNS rebinding；Origin 若存在必须与请求同源，拒绝跨站 Fetch Metadata。WebUI 静态页禁止被其他页面嵌入。

所有业务 API（包括 identity 初始化）要求固定的 `X-Tlon-WebUI: 1` 请求头，它不是秘密或认证凭据，而是让跨域网页必须经过预检的浏览器边界。SSE 使用原生 EventSource，无法设置请求头，仅该 GET 入口允许 `Sec-Fetch-Site: same-origin` 替代；其他来源或缺失两种证明均拒绝。静态页与最小 health 无需该头。所有响应（包括 SSE／错误／预检）不输出 CORS 授权头；不开放跨域预检。开发环境继续走 Vite 同源代理。

现有 GitHub webhook 保持自身 HMAC 校验，不要求 WebUI 请求头，也不因尚未设置身份而停用；仍受回环、Host 和浏览器来源边界约束。

这不是身份认证：同机程序和具有同等宿主访问能力的工具可以访问 API。本设计信任本机进程与本机操作者，仅隔离非同源网页，不承诺进程隔离或多用户安全。

## 删除与范围

删除 TLON_ADMIN_TOKEN、服务端 token 注入及校验、WebUI token 编辑器／localStorage／Bearer 透传。`/admin/*` 继续表示管理操作，统一受来源边界保护。

仅保证 WebUI。TUI、SDK、Telegram 不增设凭据、不修复可用性，不为其保留匿名或自报 username 的兼容入口。SSE session 仍是事件订阅生命周期对象，不是登录会话。

## 验收

覆盖首次设置、非法／冲突名字、重复设置、并发初始化、跨浏览器／服务重启持久化、客户端身份伪造拒绝；覆盖恶意 Origin／Host／Fetch Metadata、无来源证明请求、跨域预检、SSE 和管理接口，以及非回环启动拒绝。真实启动 dream，经 HTTP 发信并查询持久邮箱，验证 WebUI 初始化与自动进入；使用隔离 TLON_HOME，清理冒烟进程和数据。全量质量门使用 scripts/check_all.sh。
