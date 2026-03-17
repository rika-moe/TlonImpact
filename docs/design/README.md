# 设计文档总览

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **代码为准**: 代码是源头，文档是映射；冲突时以代码为准
2. **分层文档**: AGENTS.md 是地图 (~100行)，详细信息在下级文档
3. **渐进式维护**: 文档随代码演进，定期验证同步状态

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [technical.md](./technical.md) | 技术栈与依赖 | L1 |
| [config.md](./config.md) | 配置系统设计 | L1 |
| [agents.md](./agents.md) | Agent 系统设计 | L1 |
| [memory.md](./memory.md) | 记忆系统设计 | L1 |
| [messaging.md](./messaging.md) | 消息系统设计 | L1 |
| [client-server-protocol.md](./client-server-protocol.md) | Client-Server 协议设计 | L1 |
| [development-process.md](./development-process.md) | 开发流程规范 | L1 |
| [code-style.md](./code-style.md) | 代码风格规范 | L1 |
| [debugging.md](./debugging.md) | 调试与故障排除 | L1 |

---

## 验证清单

- [x] 文档结构与代码目录一致
- [x] 术语表与代码一致
- [x] 链接均可点击

---

*代码为准，文档为映射*
