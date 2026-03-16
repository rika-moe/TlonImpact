# Sandbox 层

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 职责

Docker 容器隔离执行环境

---

## 模块结构

```
sandbox/
└── docker_runtime.py      # DockerRuntime 类
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `DockerRuntime` | 容器生命周期管理 | `sandbox/docker_runtime.py` |

---

## 特性

- 每个 Agent 独立 Docker 容器
- 容器长期运行（应用重启后保留）
- 支持任意 bash 命令执行
- Skills 目录挂载到 `/workspace`
- 资源限制：内存、CPU、网络隔离

---

## 调用链

```
Agent → ToolExecutor → ExecuteBashTool → DockerRuntime → Docker SDK → docker exec → Container
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/technical.md](../../design/technical.md) | 技术栈 | L1 |

---

## 验证清单

- [x] Docker SDK 使用正确
- [x] 资源限制配置有效
- [x] 容器命名规范一致
