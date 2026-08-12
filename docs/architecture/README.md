# 架构总览

> Tlon 是**三个独立进程**组成的系统。每个进程有各自的入口、组件树和生命周期。

---

## 命令 ↔ 类名映射

| 命令 | 代码类名 | 代码路径 |
|------|---------|---------|
| `tlon dream` | `ServerApp` | `src/tlon/app/server/serverapp.py` |
| `tlon ops` | Operator (TS/Ink) | `apps/operator/` |
| `tlon mirror` | `Wizard` | `src/tlon/app/wizard/wizard.py` |

---

## 进程模型

```
launcher (main.py)
    │
    ├── tlon dream ──→ ServerApp
    │   │
    │   │  进程：常驻后台
    │   │  职责：承载 Agent、邮件投递、工具执行、记忆存储
    │   │  通信：HTTP :8080（REST + SSE）
    │   │  代码：src/tlon/app/server/
    │   │  组件：EventBus + DAG 组件树
    │   │
    ├── tlon ops ────→ Operator (TypeScript/Ink)
    │   │
    │   │  进程：交互式 TUI，连接到 dream
    │   │  职责：邮件（mail 视图，人类主界面）、运维监控、事件查看、配置查看
    │   │  通信：REST + SSE → dream
    │   │  代码：apps/operator/（由 ops 命令经 node 启动）
    │   │  组件：多视图 Ink 应用
    │   │
    └── tlon mirror ─→ Wizard
        │
        │  进程：一次性交互式向导
        │  职责：创建和管理 Agent 配置
        │  通信：直接读写文件系统（~/.tlon/{id}/config/）
        │  代码：src/tlon/app/wizard/
        │  组件：Rich 交互式菜单
```

---

## 进程间通信

```
dream ──HTTP :8080 (REST + SSE)──→ ops
mirror ──读写文件────────────────→ ~/.tlon/{id}/config/agent.yaml
```

| 方式 | 端口 | 用途 |
|------|------|------|
| REST API | 8080 | 查询、发信（POST /messages）、会话管理（sessions 端点） |
| SSE | 8080 | 事件推送（GET /events，含心跳） |
| 文件系统 | — | Wizard 读写 Agent 配置 |

---

## 文档导航

| 文档 | 描述 | 适合谁 |
|------|------|--------|
| [explicit-boundaries.md](./explicit-boundaries.md) | 架构边界规则（所有进程通用） | 所有贡献者 |
| [domains/agents.md](./domains/agents.md) | Agent 域：spec、生命周期、亚当、spawn | 想理解/修改 agent 系统 |
| [domains/base.md](./domains/base.md) | base 层：Component/Device/EventBus/ConfigService | 想理解基础设施 |

---
