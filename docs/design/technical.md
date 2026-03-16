# 技术栈设计

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **现代化 Python**: Python 3.11+，使用 uv 管理依赖
2. **异步优先**: asyncio + aiohttp，所有 IO 操作异步化
3. **显式依赖**: 构造函数注入，无全局状态
4. **事件驱动**: EventBus 解耦组件通信

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 | Python 3.11+ | 主开发语言 |
| 包管理 | uv | 现代 Python 包管理器 |
| 构建 | Hatchling | PEP 517 构建后端 |
| 异步 | asyncio + aiohttp | 异步框架和 HTTP |
| LLM | OpenAI 兼容 API | 大语言模型调用 |
| 容器 | Docker Python SDK | Agent 沙箱隔离 |
| 配置 | YAML + python-dotenv | 配置管理 |
| 日志 | structlog + rich | 结构化日志 |

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/layers.md](../architecture/layers.md) | 分层架构详解 | L2 |
| [code-style.md](./code-style.md) | 代码规范 | L1 |

---

## 验证清单

- [x] Python 版本要求 >= 3.11
- [x] 依赖版本锁定在 pyproject.toml
- [x] 异步模式贯穿所有 IO 操作
