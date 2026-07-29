# 架构总览

> Tlon 是**三个独立进程**组成的系统。每个进程有各自的入口、组件树和生命周期。

---

## 命令 ↔ 类名映射

| 命令 | 进程文档 | 代码类名 | 代码路径 |
|------|---------|---------|---------|
| `tlon dream` | [dream.md](./dream.md) | `ServerApp` | `src/tlon/app/server/serverapp.py` |
| `tlon ops` | [client.md](./client.md) | Operator (TS/Ink) | `apps/operator/` |
| `tlon mirror` | [mirror.md](./mirror.md) | `Wizard` | `src/tlon/app/wizard/wizard.py` |

---

## 进程模型

```
launcher (main.py)
    │
    ├── tlon dream ──→ ServerApp
    │   │                dream.md（内部架构）
    │   │
    │   │  进程：常驻后台
    │   │  职责：承载 Agent、消息路由、工具执行、记忆存储
    │   │  通信：REST :8080 + WebSocket :8888
    │   │  代码：src/tlon/app/server/
    │   │  组件：EventBus + DAG 组件树
    │   │
    ├── tlon ops ────→ Operator (TypeScript/Ink)
    │   │                client.md（内部架构）
    │   │
    │   │  进程：交互式 TUI，连接到 dream
    │   │  职责：运维监控、事件查看、配置查看、邮件（mail 视图，人类主界面）
    │   │  通信：WebSocket + REST → dream
    │   │  代码：apps/operator/（由 ops 命令经 node 启动）
    │   │  组件：多视图 Ink 应用
    │   │
    └── tlon mirror ─→ Wizard
                        mirror.md（内部架构）

        进程：一次性交互式向导
        职责：创建和管理 Agent 的 YAML 配置
        通信：直接读写文件系统
        代码：src/tlon/app/wizard/
        组件：Rich 交互式菜单
```

---

## 进程间通信

```
dream ──WebSocket :8888──→ ops
        └─REST :8080─────→ ops
mirror ──读写文件────────→ config/agents/*.yaml
```

| 方式 | 端口 | 用途 |
|------|------|------|
| WebSocket | 8888 | 实时双向通信、事件推送 |
| REST API | 8080 | 历史查询、健康检查、配置读取 |
| 文件系统 | — | Wizard 读写 Agent YAML 配置 |

---

## 文档导航

| 文档 | 描述 | 适合谁 |
|------|------|--------|
| [dream.md](./dream.md) | dream 进程：ServerApp、EventBus、DAG 组件 | 想理解服务端、加 Agent、改消息路由 |
| [client.md](./client.md) | ops 进程：Operator TUI（含 mail 视图） | 想改 TUI 界面、加快捷键、调样式 |
| [protocol.md](./protocol.md) | 进程间通信：REST + WebSocket 协议 | 想写新 Client、调试事件 |
| [mirror.md](./mirror.md) | mirror 进程：Wizard 配置向导 | 想改配置向导、加校验逻辑 |
| [explicit-boundaries.md](./explicit-boundaries.md) | 架构边界规则（所有进程通用） | 所有贡献者

---
