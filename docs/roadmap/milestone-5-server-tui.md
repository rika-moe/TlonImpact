# 里程碑 5: Server + TUI + Wizard

> 状态: ✅ 已完成 (🗂️ 已归档)
> 完成日期: 2026-03

---

## 目标

实现 Server-Client 分离架构，提供 TUI 客户端和配置向导。

---

## 交付物

- [x] REST API (aiohttp)
- [x] WebSocket 服务
- [x] TlonClient SDK
- [x] TUI 三种模式 (ChatRoom/Event/Agent)
- [x] Wizard 配置向导
- [x] 多客户端 Session 管理

---

## 架构要点

```
ServerApp
├── EventBus
├── MessageStore
├── MessageRouter
├── AgentRegistry
└── ClientGateway
```

---

## 命令

- `tlon server` / `tlon dream`
- `tlon tui` / `tlon intrude`
- `tlon wizard` / `tlon mirror`

---

*已归档，功能稳定运行*
