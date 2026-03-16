# 里程碑 3: Memory 系统 (Shakespeare)

**状态**: ✅ 已完成

## 目标
实现 Agent 长时记忆，支持历史对话持久化。

## 核心设计
**双层记忆架构**:
- **Working Memory**: 当前会话原始交互，固定 10 条
- **Episodic Memory**: 跨会话摘要和关键事实

## 关键交付物
- WorkingMemoryStore (JSONL)
- EpisodicMemoryStore (summaries + facts)
- MemoryManager 协调层
- Summarizer (LLM 驱动)
- Memory 工具 (rememberFact, searchMemories 等)

## 验收标准
- [x] Agent 启动时加载历史记忆
- [x] 每 10 条交互自动生成摘要
- [x] 重启后记忆不丢失
- [x] LLM 可主动记录事实、搜索记忆
