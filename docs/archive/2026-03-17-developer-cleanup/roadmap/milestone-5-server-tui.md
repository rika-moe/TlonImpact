# 里程碑 5: Server + TUI + Wizard

**状态**: ✅ 已完成

## 目标
实现 Server-Client 分离架构，提供 TUI 客户端和配置向导。

## 核心设计
**显式依赖注入**:
```
ServerApp
├── EventBus
├── MessageStore
├── MessageRouter
├── AgentRegistry
└── ClientGateway
```

## 关键交付物
- REST API (aiohttp)
- WebSocket 服务
- TlonClient SDK
- TUI 三种模式 (ChatRoom/Event/Agent)
- Wizard 配置向导
- 多客户端 Session 管理

## 命令
- `tlon server` / `tlon dream`
- `tlon tui` / `tlon intrude`
- `tlon wizard` / `tlon mirror`
