# 子系统层 (Subsystem)

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 职责

扩展功能：记忆系统、工具系统

---

## 模块结构

```
subsystem/
├── memory/                # 记忆系统 (Shakespeare)
│   ├── manager.py
│   ├── working_store.py
│   ├── episodic_store.py
│   ├── summarizer.py
│   └── models.py
└── tools/                 # 工具系统
    ├── base.py            # 工具基类
    ├── complete_dialog/   # 对话完成工具
    ├── memory/            # 记忆工具
    ├── shell/             # Shell 工具
    └── tasks/             # 任务工具 + 任务管理
```

> **Note**: Skill 系统 (skills/) 已于 2026-03-17 移除

---

## 关键类

| 类 | 职责 | 路径 | 状态 |
|----|------|------|------|
| `MemoryManager` | 长时记忆管理 | `subsystem/memory/manager.py` | ✅ |
| ~~`SkillManager`~~ | ~~技能管理~~ | ~~已移除~~ | ❌ |
| `TaskManager` | 任务管理 | `subsystem/tools/tasks/manager.py` | ✅ |
| `Tool` | 工具基类 | `subsystem/tools/base.py` | ✅ |

---

## Memory 双层架构

| 层级 | 存储 | 用途 |
|------|------|------|
| Working | `working/interactions.jsonl` | 当前会话原始交互 |
| Episodic | `episodic/summaries.jsonl` | 跨会话摘要 |
| Facts | `episodic/facts.jsonl` | 关键事实 |

---

## 文档索引
n| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/memory.md](../../design/memory.md) | 记忆系统设计 | L1 |

---

## 验证清单

- [x] Memory 双层架构与代码一致
- [x] ~~Skill 系统~~ 已于 2026-03-17 移除
- [x] Tool 基类设计正确
