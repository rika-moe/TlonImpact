# 记忆系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 核心理念

1. **双层记忆**: Working Memory（短期）+ Episodic Memory（长期）
2. **自动摘要**: Working Memory 满 10 条或空闲 30 分钟触发摘要
3. **关键事实**: 从对话提取持久化事实
4. **Context 注入**: 自动将相关记忆注入 LLM Context

---

## 记忆分层

| 层级 | 存储 | 触发时机 | 用途 |
|------|------|----------|------|
| Working | `working/interactions.jsonl` | 每次交互 | 当前会话上下文 |
| Episodic | `episodic/summaries.jsonl` | 摘要生成 | 跨会话历史 |
| Facts | `episodic/facts.jsonl` | 事实提取 | 用户偏好、关键信息 |

---

## 摘要流程

```
Working Memory 满/空闲 → LLM 生成摘要 → 提取关键事实 → 存入 Episodic → 清空 Working
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/modules.md](../architecture/modules.md) | 模块架构（含 MemoryModule） | L2 |

---

## 验证清单

- [x] 双层架构与代码一致
- [x] 存储格式为 JSONL
- [x] 摘要触发条件正确
