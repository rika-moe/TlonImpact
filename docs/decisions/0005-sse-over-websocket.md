# ADR 0005：SSE 而非 WebSocket

> 后续变更（2026-10-31）：Operator TUI、Python SDK 与 Telegram 已[退役删除](legacy-client-retirement.md)；下文涉及它们的实现和验证记录属于历史。当前客户端为 WebUI，包结构与入口以[当前架构](../architecture.md)为准。

> 阅读定位：本文保留当时的选择与理由；当前设计见 [server-protocol](../design/server-protocol.md)。当前适用范围：REST + SSE 通信选择。完整关系见 [决策索引](README.md)。

- 状态：已接受（回溯记录，2026-08-15）
- 相关：[../design/server-protocol.md](../design/server-protocol.md)（服务端协议设计）

## 背景

人类客户端（Operator TUI、Python SDK、任意第三方客户端）需要实时收到邮件投递与系统/agent 事件，服务端到客户端必须有一条推送通道。候选是 WebSocket、SSE（Server-Sent Events）、客户端轮询。

## 决策

对外协议为纯 HTTP REST + SSE：下行推送走 `GET /events` 的 SSE 长连接（协议 v2），上行写操作走普通 REST（`POST /messages` 等）。不提供 WebSocket 端点。实现见 [src/tlon_server/api/rest/](../../src/tlon_server/api/rest) 包（ADR 0019 拆分后路径），设计细节见 [docs/design/server-protocol.md](../design/server-protocol.md)。

## 理由

- **推送是单向的**：服务端 → 客户端只有事件通知一个方向；客户端 → 服务端的操作（发信、建会话、改过滤）用普通 POST 即可完整表达。为这个拓扑引入全双工通道是无谓的复杂度。
- **HTTP 友好**：SSE 就是一条普通的 HTTP 长响应。任何 HTTP 客户端（curl、脚本、fetch）无需额外库即可接入，代理、防火墙、抓包调试全部按普通 HTTP 工作——与「任意 HTTP 客户端即可接入」的协议目标一致。
- **重连语义现成**：SSE 是文本协议、可重建，配合「断连即离线、重连重建会话」的会话模型，断线恢复逻辑在客户端几十行就能写完（当时 Operator 的 `sseClient.ts` 的指数退避重连）。
- **协议面小**：没有帧格式、握手升级、双向心跳要定义和维护；事件帧就是一行 JSON。协议的唯一版本号（`SESSION_PROTOCOL_VERSION = "2.0.0"`）在会话建立时声明，演进空间清晰。

## 备选方案

- **WebSocket**：全双工能力本场景用不到；协议面大（握手、帧、ping/pong、关闭码），客户端需要额外库；调试和代理支持不如纯 HTTP 直观。
- **客户端轮询**：实现最简单，但实时性受轮询间隔限制，且空转请求在长时间挂机的 TUI 场景是无谓负载；作为降级手段始终可用（`GET /inbox` 随时可拉），不需要做成主通道。

## 后果

- 正面：服务端只维护一个 aiohttp HTTP 栈；当时的 SDK（ADR 0019 拆分后的 `tlon_client/sdk`）与 TUI 都不依赖 WebSocket 库；协议可被任何 HTTP 工具完整复现。
- 负面：客户端不能经推送通道上行——但这本来就不是需求，上行 POST 语义更清晰；SSE 连接在 HTTP/1.1 下占用每域连接槽，本场景是本地单用户客户端，无感；断连期间的推送事件丢失，客户端以重连后重新拉取 `GET /inbox` 和会话快照补齐（邮件是持久事实，丢不了；瞬时事件可丢）。