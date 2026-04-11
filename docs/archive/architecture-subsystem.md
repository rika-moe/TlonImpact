# 功能子模块 (Memory & Tools)

> 状态: ✅ 已验证
> 最后验证: 2026-04-11
> 历史: 原 `subsystem/` 目录已拆分为独立的 `memory/` 和 `tools/` 模块

---

## 职责

扩展功能：记忆系统、工具系统

---

## 模块结构

代码已重构为独立模块（原 `subsystem/` 已拆分）：

```
tlon/
├── memory/                # 记忆系统模块 (Shakespeare)
│   ├── manager.py         # MemoryManager
│   ├── working_store.py   # 工作记忆存储
│   ├── episodic_store.py  # 情景记忆存储
│   ├── summarizer.py      # 摘要生成器
│   └── models.py          # 数据模型
│
└── tools/                 # 工具系统模块
    ├── base.py            # Tool 抽象基类
    ├── complete_dialog/   # 对话完成工具
    ├── memory/            # 记忆相关工具
    ├── shell/             # Shell 工具 (依赖 Sandbox)
    └── tasks/             # 任务工具 + TaskManager
```

---

## 关键类

| 类 | 职责 | 路径 | 状态 |
|----|------|------|------|
| `MemoryManager` | 长时记忆管理 | `memory/manager.py` | ✅ |
| ~~`SkillManager`~~ | ~~技能管理~~ | ~~旧实现已移除，新实现待重做~~ | ⏳ |
| `TaskManager` | 任务管理 | `tools/tasks/manager.py` | ✅ |
| `Tool` | 工具基类 | `tools/base.py` | ✅ |

---

## Memory 双层架构

| 层级 | 存储 | 用途 |
|------|------|------|
| Working | `working/interactions.jsonl` | 当前会话原始交互 |
| Episodic | `episodic/summaries.jsonl` | 跨会话摘要 |
| Facts | `episodic/facts.jsonl` | 关键事实 |

---

## 模块间关系

```
AgentModule
    ├── MemoryModule (依赖注入)
    │       └── MemoryManager
    │
    └── ToolExecutor
            └── 注册各种 Tools
                    ├── memory/remember_fact.py ──► MemoryModule
                    ├── memory/search_memories.py ──► MemoryModule
                    ├── tasks/*.py ──► TaskManager
                    └── shell/execute_bash.py ──► SandboxModule
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [模块架构](../modules.md) | 整体模块架构说明 | L1 |
| [../../design/memory.md](../../design/memory.md) | 记忆系统设计 | L1 |

---

## 验证清单

- [x] `subsystem/` 已拆分为独立 `memory/` 和 `tools/` 模块
- [x] Memory 双层架构与代码一致
- [ ] ~~Skill 系统~~ 旧实现已移除，新实现待重做
- [x] Tool 基类设计正确
- [x] 各模块 `__init__.py` 正确暴露公开接口
