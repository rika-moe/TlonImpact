# 旧客户端退役

日期：2026-10-31。状态：已接受。

Operator TUI、Python SDK 和 Telegram 客户端已停止维护，删除 `apps/operator/` 与整个 `src/tlon_client/`，同时移除 `tlon ops`、`tlon telegram` 入口、专属配置、依赖、测试和构建步骤，不保留兼容层。

WebUI 继续使用自己的 REST/SSE 实现；服务端邮件、会话和协议能力保留。包依赖方向收敛为 `tlosure ← tlon ← tlon_server ← tlon_launcher`。

删除客户端专属使用及设计文档；保留历史 ADR 中仍有效的架构决策，注明客户端退役并修复失效引用。本决策覆盖 ADR 0019 中关于客户端包及其分发的当前规划。

SDK 专属测试随实现删除；Windows 真实邮件冒烟改用现有 HTTP 测试辅助函数，继续验证真实 dream 进程到持久化收件箱的链路。验收执行完整 `scripts/check_all.sh`，包括真实进程邮件冒烟。
