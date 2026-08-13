# Memory 层

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

记忆系统：working（近期交互滚动窗口）+ episodic（长期摘要与事实）两级存储、
LLM 摘要/事实提取、context block 组装。纯被动服务——所有调用由 agents 域注入，
memory 不知道 mailbox / organization / 事件总线。

---

## 模块结构

```
memory/
├── base.py           # MemoryStore ABC：add/get/get_recent/delete/clear/count + 默认 search_by_keyword
├── models.py         # 数据模型：MemoryConfig / Interaction / Summary / Fact / ContextBlock / FactType
├── working_store.py  # WorkingMemoryStore：近期交互滚动窗口，JSONL
├── episodic_store.py # EpisodicMemoryStore：长期摘要 + 事实，JSONL ×2
├── summarizer.py     # Summarizer：LLM 摘要生成 + 事实提取（独立 LLMClient）
├── manager.py        # MemoryManager：对外统一门面，协调两个 store + Summarizer
└── __init__.py       # 仅 docstring，无 re-export
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `MemoryStore` | 存储后端抽象基类 | `memory/base.py:24` |
| `MemoryManager` | 统一门面：load / 存交互 / 触发摘要 / context blocks / 工具操作 | `memory/manager.py:46` |
| `WorkingMemoryStore` | 滚动窗口（默认 10 条）+ 内存 cache + `asyncio.Lock` | `memory/working_store.py:53` |
| `EpisodicMemoryStore` | summaries（默认 20）+ facts（默认 50），supersede 机制 | `memory/episodic_store.py:37` |
| `Summarizer` | 两次 LLM 调用（摘要 + 事实提取），timeout=30s，失败全回落 | `memory/summarizer.py:31` |
| `MemoryConfig` | 阈值配置（to_dict/from_dict 分组 JSON） | `memory/models.py:37` |
| `Fact` | 三元组 + `superseded_by` 标记 | `memory/models.py:230` |
| `ContextBlock` | 记忆注入块，`to_openai_message()` 输出 system 消息 | `memory/models.py:297` |

---

## 存储布局与持久化

- 根目录 `~/.tlon/{agent_id}/memory/`（`base/paths.py:114`），下分：
  - `config.json`（`paths.py:219`）
  - `working/interactions.jsonl`（`paths.py:222`；一行一条 Interaction）
  - `episodic/summaries.jsonl` + `episodic/facts.jsonl`（`paths.py:227`）
- **增量 append + 原子重写双轨**：add 路径直接 append 一行
  （`working_store.py:180-185`、`episodic_store.py:207-213`）；变更类操作
  （trim / delete / supersede / remove_by_ids）走 tmp 文件 + `os.replace` 原子重写
  （`working_store.py:137-150`、`episodic_store.py:159-183`），重写失败清理 tmp；
  append 失败时 fallback 到全量重写（`working_store.py:184-185`）。
- 读路径全部走内存 cache（load 一次性读盘，坏行跳过记 warning）；写路径
  cache + 文件双写，每 store 一把 `asyncio.Lock`（`working_store.py:88`、
  `episodic_store.py:80`）。

---

## 核心机制

- **摘要触发两路**：working 满（`_check_summary_trigger`，`manager.py:306-316`）+
  idle timeout（`check_idle_timeout`，`manager.py:318-349`，由 scheduler 主循环每轮调用）。
- **snapshot + delta-clear**（不变式）：`generate_summary` 先在 store 锁内快照 working
  全量 id → LLM 调用不持锁 → 完成后 `remove_by_ids` 只删快照项，LLM 期间新进的交互
  不丢（`manager.py:472-566`；`remove_by_ids` 在 `working_store.py:258-266`）。
- **LLM 永不阻塞记忆**：Summarizer 异常 / JSON 解析失败 → `_generate_fallback_summary`
  （纯文本兜底，`summarizer.py:354-370`）；`llm_config=None` 时 `summarizer=None`，
  全程走 fallback（`manager.py:104-107`）。
- **Fact supersede**：同 (subject, predicate, object) 的新 fact 把旧 fact 标
  `superseded_by`；因改动了已落盘的行，supersede 发生时整文件原子重写而非 append
  （`episodic_store.py:276-311`）。读侧 `get_recent_facts` 排除 superseded
  （`episodic_store.py:337-339`）。
- **搜索**：纯关键字包含匹配（summaries 匹配 keywords 字段 + summary_text；facts
  匹配拼接文本），无向量检索（`episodic_store.py:454-494`）。
- 无 session id 时自动生成 `%Y%m%d_%H%M%S`（`manager.py:239-240`）。
- **兼容残留**：`MemoryManager.__init__` 的 `data_dir` 参数被忽略（`manager.py:75`），
  两个 store 的 `storage_path` 同样被忽略
  （`working_store.py:79`、`episodic_store.py:67`）——路径权威全在 TlonPaths。

---

## 与 agents 域的接口

- **构造**：`AgentRegistry._assemble_cognitive` 每个认知 agent 一个 MemoryManager
  （`agents/agent_registry.py:558-563`）；确定性成员不建（D1）。
- **写入**：`EffectExecutor.save_interaction`——session 完成时 `add_interaction`，
  受 `rule.loop.save_interaction` 门控，随后 `reflection.note_interaction()`
  （`agents/executor.py:125-142`）。
- **读取**：`ContextBuilder.build_knowledge_context` → `build_context_blocks()`
  （`agents/context_builder.py:91-104`），经 `:context` 的 `knowledge` section 注入
  user 消息（`context_builder.py:118-121`）。
- **触发**：`SessionScheduler` 主循环每轮 `check_idle_timeout()`（`agents/scheduler.py:170`）。
- **反思**：`ReflectionEngine(memory, llm_client, rule)`（`agents/reflection.py:71`）——
  开关/间隔在 spec 的 `loop.reflect`，`reflection_min_interactions` 留在 MemoryConfig
  （`models.py:72-75`）；消费 `get_memory_digest`（`manager.py:751`）并可执行
  record_fact / delete_fact / consolidate / noop（`reflection.py:213-241`）。
  反思在后台 task 跑，不阻塞主循环（`reflection.py:110-117`）。
- **工具**：`tools/memory/{remember_fact,search_memories,forget_memory,compact}.py`，
  注册于 `agent_registry.py:600-603`；compact 即
  `generate_summary(force=True, instructions=...)`（`tools/memory/compact.py:64-79`）。

---

## 边界

- import-linter 契约：**memory 只依赖 base**（`pyproject.toml:164-176`）；
  LLMClient 复用 `base/llm_client.py`（与 agents 共用）。
- 测试：`src/tests/memory/test_working_store.py`、`src/tests/memory/test_episodic_store.py`、
  `src/tests/tools/test_compact_tool.py`。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/memory.md](../../design/memory.md) | 记忆系统设计（Shakespeare） | L1 |

---

## 验证清单

- [x] 两 store + Summarizer + MemoryManager 门面结构与代码一致
- [x] 增量 append + 原子重写双轨持久化
- [x] snapshot + delta-clear 不变式（LLM 期间新进交互不丢）
- [x] LLM 失败全回落，永不阻塞记忆写入
- [x] import-linter 契约：memory 只依赖 base
