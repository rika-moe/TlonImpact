# 开发计划 (Roadmap)

## 项目概述

**项目名称**: Tlon  
**目标**: 构建一个基于群聊的多 Agent 协作系统，支持 Agent Skills 标准  
**技术栈**: Python 3.11+, asyncio, Docker, OpenAI API  
**开发方式**: 分 4 个里程碑迭代开发

---

## 开发原则

1. **优先级**: 核心流程优先，边缘情况后续处理
2. **解耦**: 模块间通过接口交互，便于独立开发和测试
3. **可测试**: 每个模块编写时同步编写测试
4. **快速验证**: 每个里程碑有可运行的 Demo
5. **不追求完美**: 先跑通，再优化

---

## 里程碑 1: 单 Agent + CLI 交互

**目标**: 验证核心架构，实现最小可用版本

**周期**: 预计 3-5 天

### 任务清单

#### Phase 1.1: 项目骨架搭建 (Day 1)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 1.1.1 创建项目目录结构 | P0 | - | 目录结构符合架构文档 | ✅ |
| - [x] 1.1.2 配置 pyproject.toml | P0 | - | 可安装，依赖明确 | ✅ |
| - [x] 1.1.3 配置环境变量管理 | P1 | - | `.env.example` 和加载逻辑 | ✅ |
| - [x] 1.1.4 日志配置 | P1 | - | 统一日志格式和级别 | ✅ |

#### Phase 1.2: 核心层实现 (Day 1-2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 1.2.1 Message 数据类实现 | P0 | - | 支持 to_dict/from_dict/to_openai_message | ✅ |
| - [x] 1.2.2 Event 和 EventType 定义 | P0 | 1.2.1 | 定义所有事件类型 | ✅ |
| - [x] 1.2.3 EventBus 基础实现 | P0 | 1.2.2 | 支持 publish/subscribe，单元测试通过 | ✅ |
| - [x] 1.2.4 ChatRoom 基础实现 | P1 | 1.2.1, 1.2.3 | 支持 join/leave/send_message | ✅ |
| - [x] 1.2.5 ChatRoom 持久化 | P1 | 1.2.4 | 消息保存到 JSONL，启动可加载 | ✅ |

#### Phase 1.3: Agent 基础组件 (Day 2-3)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 1.3.1 LLMClient 实现 | P0 | - | 支持 OpenAI 格式，带重试机制 | ✅ |
| - [x] 1.3.2 DockerRuntime 实现 | P0 | - | 容器创建/执行/停止 | ✅ |
| - [x] 1.3.3 Tool 基类和工具注册 | P0 | - | 支持工具定义和调用 | ✅ |
| - [x] 1.3.4 ExecuteBashTool 实现 | P0 | 1.3.2, 1.3.3 | 可在 Docker 中执行命令 | ✅ |
| - [x] 1.3.5 SendMessageTool 实现 | P0 | 1.2.3, 1.3.3 | 可向 EventBus 发消息 | ✅ |

#### Phase 1.4: Agent 主类实现 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 1.4.1 AgentConfig 数据类 | P0 | - | 配置字段完整 | ✅ |
| - [x] 1.4.2 Agent 初始化流程 | P0 | 1.2.3, 1.3.x | 能启动 Docker 和订阅事件 | ✅ |
| - [x] 1.4.3 Agent 消息处理流程 | P0 | 1.4.2 | think → tool execute → response | ✅ |
| - [x] 1.4.4 SystemPrompt 构建 | P1 | 1.4.3 | 包含角色描述和工具说明 | ✅ |
| - [x] 1.4.5 Agent 配置 YAML | P1 | 1.4.1 | 单个 Agent 配置示例 | ✅ |

#### Phase 1.5: CLI Adapter (Day 4-5)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 1.5.1 AdapterInterface 抽象 | P0 | - | 定义统一接口 | ✅ |
| - [x] 1.5.2 CLIAdapter 实现 | P0 | 1.5.1, 1.2.x | 支持输入和格式化输出 | ✅ |
| - [x] 1.5.3 应用主入口 | P0 | 1.5.2 | 可启动并运行 | ✅ |
| - [x] 1.5.4 集成测试 | P1 | 全部 | 端到端对话流程 | ✅ |

### 里程碑 1 验收标准

- [x] 可以运行: `python -m minions`
- [x] 在 CLI 中输入消息
- [x] Agent 响应消息（可能调用工具）
- [x] 可以执行 bash 命令（如 echo, ls）
- [x] 消息历史保存到 data/ 目录
- [x] 重启后历史记录还在

### 里程碑 1 Demo 场景

测试用例（完成后勾选）：

- [x] **对话测试** ✅
  ```
  > 你是谁？
  [Agent] 我是开发-A，一个开发者，负责代码编写和 Bug 修复...
  ```

- [x] **工具调用测试** ✅
  ```
  > 帮我执行 echo "Hello World"
  [Agent] 我来执行一下
  [Agent] 执行结果：Hello World
  ```

- [x] **持久化测试** ✅
  - 重启应用后，历史消息仍然存在（95 条记录已验证）

---

## 里程碑 2: Skills 系统（Agent Skills 标准）

**目标**: 实现符合 [Agent Skills](https://github.com/agentskills/agentskills) 标准的能力模块系统

**周期**: 预计 5-7 天

### Agent Skills 标准概述

本项目采用 Agent Skills 开放标准：

- **Skill 是文件夹**: 每个 Skill 是一个目录，包含 `SKILL.md` 作为入口
- **渐进式披露**: 
  1. **Discovery**: 启动时只加载 frontmatter 元数据（name/description）
  2. **Activation**: 需要时加载完整 `SKILL.md` 内容
  3. **Execution**: 按需执行 `scripts/` 中的脚本或读取 `references/`
- **自包含**: Skill 可以包含 `scripts/`、`references/`、`assets/` 子目录

### 任务清单

#### Phase 2.1: Skill 核心机制 (Day 1-2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.1.1 SkillManager 类实现 | P0 | - | Discovery/Activation/Execution 三阶段 | ✅ |
| - [x] 2.1.2 SKILL.md 解析器 | P0 | 2.1.1 | 解析 frontmatter 和 Markdown 内容 | ✅ |
| - [x] 2.1.3 Skill 发现机制 | P0 | 2.1.2 | 扫描 skills/ 目录，加载元数据 | ✅ |
| - [x] 2.1.4 Skill 激活机制 | P0 | 2.1.3 | 按需加载完整 SKILL.md 内容 | ✅ |

#### Phase 2.2: Skill 集成到 Agent (Day 2-3)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.2.1 Agent 配置支持 skills 列表 | P0 | 2.1.x | agents.yaml 指定 Skill 名称 | ✅ |
| - [x] 2.2.2 启动时 Skill Discovery | P0 | 2.2.1 | Agent 启动时扫描配置的技能 | ✅ |
| - [x] 2.2.3 activateSkill 工具 | P0 | 2.2.2 | LLM 可调用激活 Skill | ✅ |
| - [x] 2.2.4 loadSkillResource 工具 | P1 | 2.2.3 | 读取 references/、assets/ 文件 | ✅ |
| - [x] 2.2.5 deactivateSkill 工具 | P1 | 2.2.3 | 关闭技能，释放上下文资源 | ✅ |
| - [x] 2.2.6 Skill 内容注入 Prompt | P1 | 2.2.3 | 激活后 Skill 指令加入上下文 | ✅ |

#### Phase 2.3: Skill 脚本执行 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.3.1 Skill 脚本执行机制 | P0 | - | 使用 executeBashCommand 执行 scripts/ | ✅ |
| - [x] 2.3.2 Python 脚本支持（PEP 723） | P1 | 2.3.1 | 支持内联依赖声明 | ✅ |
| - [x] 2.3.3 Bash 脚本支持 | P1 | 2.3.1 | 支持 .sh 脚本，路径格式: ./skills/{name}/scripts/xxx.sh | ✅ |
| - [x] 2.3.4 脚本输出捕获 | P1 | 2.3.1 | stdout/stderr 返回给 Agent | ✅ |

#### Phase 2.4: 基础 Skills 开发 (Day 4-5)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.4.1 file-operations Skill | P0 | 2.3.x | read/write/list 文件操作 | ✅ |
| - [x] 2.4.2 git-operations Skill | P1 | 2.3.x | clone/commit/push 操作 | ✅ |
| - [x] 2.4.3 Skill 脚本测试 | P1 | 2.4.x | 脚本可独立运行并测试 | ✅ |

#### Phase 2.5: Skill 验证与文档 (Day 5-6)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.5.1 Skill 验证工具 | P1 | - | 检查 SKILL.md 格式和必填字段 | ✅ |
| - [x] 2.5.2 Skill 开发文档 | P1 | - | 如何编写 SKILL.md 的文档 | ✅ |
| - [x] 2.5.3 目录结构规范化 | P1 | - | skills/ 目录组织规范 | ✅ |

#### Phase 2.6: 集成测试 (Day 6-7)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 2.6.1 Skill Discovery 测试 | P0 | 全部 | 正确扫描和加载元数据 | ✅ |
| - [x] 2.6.2 Skill Activation 测试 | P0 | 全部 | LLM 可触发激活 | ✅ |
| - [x] 2.6.3 Skill Execution 测试 | P0 | 全部 | 脚本正确执行 | ✅ |
| - [x] 2.6.4 多 Skill 同时加载测试 | P1 | 全部 | 多个 Skills 工作正常 | ✅ |

### 里程碑 2 验收标准

- [x] 符合 Agent Skills 标准的目录结构（`SKILL.md` + `scripts/` + `references/`）
- [x] 启动时执行 Skill Discovery，只加载元数据
- [x] LLM 可通过工具激活 Skill，加载完整内容
- [x] Skill 脚本可在 Docker 容器中执行
- [x] 有 2-3 个可用的基础 Skills（file-operations, git-operations）
- [x] 有 Skill 开发文档和验证工具

### 里程碑 2 Demo 场景

#### Demo 1: Skill Discovery 测试
```
启动日志：
[ INFO] Discovered skill: file-operations
[ INFO] Discovered skill: git-operations

Agent 知晓可用技能：
人类: @开发-A 你能做什么？
开发-A: 我可以使用以下技能：
        - file-operations: Read, write, and manipulate files...
        - git-operations: Perform Git version control operations...
```

#### Demo 2: Skill Activation 测试
```
人类: @开发-A 帮我查看一下配置文件
开发-A: [activateSkill: file-operations]  # 自动激活
开发-A: 我来读取配置文件内容...
       [executeBashCommand] python3 skills/file-operations/scripts/read_file.py config.yaml
```

#### Demo 3: Skill 脚本执行测试
```
# skills/file-operations/SKILL.md
---
name: file-operations
description: File operations skill
---

## Available scripts
- `scripts/read_file.py` - Read file contents

## Usage
```bash
python3 scripts/read_file.py <file_path>
```
```

```python
# skills/file-operations/scripts/read_file.py
# /// script
# dependencies = []
# requires-python = ">=3.11"
# ///

import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file_path")
    args = parser.parse_args()
    content = Path(args.file_path).read_text()
    print(content)

if __name__ == "__main__":
    main()
```

#### Demo 4: 多 Skill 协作测试
```
人类: @开发-A 克隆这个仓库并查看 README
开发-A: [activateSkill: git-operations]
       [executeBashCommand] python3 skills/git-operations/scripts/clone.py github.com/example/repo
       
开发-A: [activateSkill: file-operations]
       [executeBashCommand] python3 skills/file-operations/scripts/read_file.py repo/README.md
       
开发-A: 仓库已克隆，README 内容如下：...
```

---

## 里程碑 3: Memory 系统

**目标**: 实现 Agent 长时记忆，支持历史对话持久化和上下文增强

**设计文档**: [memory.md](../01-architecture/memory.md)

**周期**: 预计 4-5 天

### 核心设计

采用**双层记忆架构**:
- **Working Memory**: 当前会话的原始交互（含 tool_results），固定 10 条，滚动窗口
- **Episodic Memory**: 跨会话的会话摘要和关键事实，长期持久化

Context 四层结构: System → Episodic(摘要+事实) → Working+ChatRoom → Immediate

### 任务清单

#### Phase 3.1: Memory 核心存储 (Day 1-2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 3.1.1 WorkingMemoryStore 实现 | P0 | - | 交互记录增删查，JSONL 存储 | ✅ |
| - [x] 3.1.2 EpisodicMemoryStore 实现 | P0 | 3.1.1 | 摘要和事实分类存储 | ✅ |
| - [x] 3.1.3 MemoryManager 协调层 | P0 | 3.1.2 | 统一接口，加载/保存逻辑 | ✅ |
| - [x] 3.1.4 配置文件设计 | P1 | 3.1.3 | config.json 结构设计 | ✅ |

**Phase 3.1 实施完成 ✅ (含 3.2.1-3.2.2)**

创建了新的 `minions/memory/` 模块，替换了旧的 `agents/memory.py`:

**文件结构:**
```
minions/memory/
├── __init__.py          # 模块导出
├── models.py            # 数据模型 (Interaction, Summary, Fact, ContextBlock, MemoryConfig)
├── base.py              # MemoryStore 抽象基类
├── working_store.py     # WorkingMemoryStore - JSONL 存储近期交互
├── episodic_store.py    # EpisodicMemoryStore - 摘要/事实分类存储
└── manager.py           # MemoryManager - 主接口
```

**存储结构:**
```
data/agents/{agent_id}/memory/
├── config.json              # Memory 配置
├── working/
│   └── interactions.jsonl   # 工作记忆（原始交互）
└── episodic/
    ├── summaries.jsonl      # 会话摘要
    └── facts.jsonl          # 关键事实
```

**整合更新:**
- ✅ `agents/config.py`: 导入新的 `MemoryConfig`
- ✅ `agents/agent.py`: 使用新的 `MemoryManager` 替换旧的 `Memory`
- ✅ `main.py`: 更新配置加载以支持新的 `MemoryConfig`
- ✅ `config/agents.yaml`: 更新内存配置示例
- ✅ 旧的 `agents/memory.py` 已删除

#### Phase 3.2: Context 构建与注入 (Day 2-3)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 3.2.1 ContextBlock 数据结构 | P0 | - | 定义记忆块格式 | ✅ |
| - [x] 3.2.2 build_context_blocks() 实现 | P0 | 3.2.1 | 按优先级组装记忆块 | ✅ |
| - [x] 3.2.3 Agent._build_system_prompt() 集成 | P0 | 3.2.2 | 注入 Memory 到 System Prompt | ✅ |
| - [ ] 3.2.4 优化记忆格式化输出 | P1 | 3.2.3 | 更美观的 Prompt 格式 | ⬜ |

**说明**: ContextBlock 和 build_context_blocks() 已在 3.1 中实现，并已集成到 Agent._build_system_prompt() 中。

#### Phase 3.3: 摘要生成与事实提取 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 3.3.1 会话摘要生成 (LLM) | P0 | - | 10 条交互触发，生成自然语言摘要 | ✅ |
| - [x] 3.3.2 关键事实提取 (LLM) | P1 | 3.3.1 | 从交互中提取结构化事实 | ✅ |
| - [x] 3.3.3 触发机制实现 | P0 | 3.3.1 | 数量阈值/空闲超时/主动触发 | ✅ |
| - [x] 3.3.4 Working Memory 清理 | P1 | 3.3.3 | 摘要生成后清空或归档 | ✅ |

**Phase 3.3 实施完成 ✅**

创建了 `Summarizer` 类使用 LLM 生成智能摘要和提取事实：

**文件结构:**
```
minions/memory/
├── ...
└── summarizer.py      # 新增：LLM 驱动的摘要和事实提取
```

**主要功能:**
- `Summarizer.generate_summary()`: 使用 LLM 生成自然语言摘要
- `Summarizer.extract_facts()`: 从交互中提取结构化事实
- `Summarizer.process_interactions()`: 批量处理，同时生成摘要和提取事实

**触发机制:**
1. **数量阈值**: Working Memory 满时自动触发 (`working.is_full()`)
2. **空闲超时**: 每次处理消息前检查 (`check_idle_timeout()`)
3. **主动触发**: `generate_summary(force=True)` 可强制生成

**清理逻辑:**
- 摘要生成后自动调用 `working.archive_and_clear()`
- 已归档的交互从 Working Memory 移除，存入 Episodic Memory

**集成更新:**
- ✅ `MemoryManager`: 接受 `llm_config` 参数，初始化 `Summarizer`
- ✅ `Agent`: 传入 `llm_config` 到 `MemoryManager`
- ✅ `Agent._process_message()`: 添加 `check_idle_timeout()` 调用

#### Phase 3.4: LLM 工具与用户命令 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 3.4.1 rememberFact 工具 | P1 | - | LLM 主动记录事实 | ✅ |
| - [x] 3.4.2 searchMemories 工具 | P1 | 3.4.1 | 关键词搜索记忆 | ✅ |
| - [x] 3.4.3 generateSummary 工具 | P1 | 3.4.2 | LLM 主动触发摘要 | ✅ |
| - [x] 3.4.4 forget 工具 | P2 | 3.4.3 | LLM 主动遗忘 | ✅ |
| - [x] 3.4.5 User 命令 (/history /summary /facts) | P2 | - | 用户查看记忆 | ✅ |

**Phase 3.4 实施完成 ✅**

创建了 4 个 Memory 工具供 LLM 调用：

```
minions/tools/
├── remember_fact.py       # rememberFact: 记录重要事实
├── search_memories.py     # searchMemories: 搜索记忆
├── generate_summary.py    # generateSummary: 主动触发摘要
└── forget_memory.py       # forget: 删除记忆
```

**用户斜杠命令** (在 `main.py` 中实现):
- `/history [n]` - 显示 Working Memory 历史记录
- `/summary` - 手动触发摘要生成
- `/facts` - 显示已知事实

**集成更新:**
- ✅ `Agent._register_tools()`: 注册 4 个 Memory 工具
- ✅ `Agent._build_system_prompt()`: 添加 Memory 工具说明
- ✅ `Application._handle_slash_command()`: 处理用户命令

#### Phase 3.5: 集成与测试 (Day 4-5)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 3.5.1 Agent 启动加载记忆 | P0 | 全部 | 启动时正确加载 | ✅ |
| - [x] 3.5.2 跨会话记忆测试 | P0 | 3.5.1 | 重启后记忆不丢失 | ✅ |
| - [x] 3.5.3 Context 注入测试 | P0 | 3.5.2 | 记忆正确出现在 Prompt | ✅ |
| - [x] 3.5.4 摘要生成测试 | P1 | 3.5.3 | 满足条件时正确生成 | ✅ |

**Phase 3.5 实施完成 ✅**

**测试验证:**
- ✅ Agent 启动时调用 `memory.load()` 加载历史记忆
- ✅ Working Memory 保存到 `data/agents/{id}/memory/working/interactions.jsonl`
- ✅ Episodic Memory 保存到 `data/agents/{id}/memory/episodic/{summaries,facts}.jsonl`
- ✅ Context 构建包含：摘要(最多5条) + 事实(最多10条) + 工作记忆(最多5条)
- ✅ 记忆内容正确注入 System Prompt
- ✅ 重启后记忆不丢失

### 里程碑 3 验收标准

- [x] Agent 启动时从文件加载历史记忆
- [x] Working Memory 保存最近 10 条交互（含 tool_results）
- [x] 每 10 条交互或空闲 30 分钟自动生成会话摘要
- [x] Episodic Memory 保存会话摘要和关键事实
- [x] Context 构建包含：摘要(最多5条) + 事实(最多10条) + 工作记忆(最多5条)
- [x] 重启应用后记忆不丢失，能正确恢复对话上下文
- [x] LLM 可通过工具主动记录事实、搜索记忆、生成摘要
- [x] 用户可通过命令查看历史 (/history) 和摘要 (/summary)

### 里程碑 3 Demo 场景

#### Demo 1: 跨会话记忆持久化
```
第一次对话：
> 我叫张三，请记下来
[Agent] 好的，我已经记住您的名字是张三 [rememberFact: 用户名字叫张三]

重启应用后：
> 你还记得我叫什么吗？
[Agent] 当然，根据之前的记录，您的名字是张三
```

#### Demo 2: 工具执行历史记忆
```
第一次对话：
> 帮我查看 config.yaml
[Agent] 我来查看 [executeBash: cat config.yaml]
[Agent] 配置内容是: database_url=postgres://localhost

第二次对话（同一会话）：
> 刚才的数据库配置是什么？
[Agent] 根据刚才的查看，数据库配置是 postgres://localhost
```

#### Demo 3: 会话摘要生成
```
第 10 条交互后：
[Agent] [自动生成会话摘要]

新会话：
> 上次我们做了什么？
[Agent] 根据上次的会话记录，我们完成了：
       1. 查看了项目配置文件
       2. 检查了数据库连接设置
       3. 确认使用 PostgreSQL 数据库
```

---

## 里程碑 4: 多 Agent 系统

**目标**: 实现完整的多 Agent 团队，支持 Agent 间协作

**设计文档**: [multi-agent.md](../01-architecture/multi-agent.md)

**周期**: 预计 6-7 天

### 核心设计

- **响应规则**: 客服主动+被动响应，其他角色仅被动响应（被@时）
- **启动策略**: 客服常驻，其他角色延迟启动（首次被@时唤醒）
- **循环@解决**: 依赖 System Prompt 自我节制，不设计复杂状态跟踪
- **并发模型**: Agent 间并行（独立 Docker），Agent 内串行（消息队列）
- **团队协作**: 所有 Agent Prompt 注入团队信息表，知晓同事职责

### 任务清单

#### Step 1: 配置扩展（1 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.1.1 修改 agents.yaml 支持 5 角色完整配置 | P0 | - | 包含客服、项目经理、开发A/B、运维、产品经理 | ✅ |
| - [x] 4.1.2 在 AgentConfig 中添加 proactive 字段 | P0 | 4.1.1 | 标记主动响应角色 | ✅ |
| - [x] 4.1.3 修改 main.py 使用多 Agent 配置加载 | P0 | 4.1.2 | 能加载 5 个角色配置 | ✅ |

#### Step 2: 响应规则（1 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.2.1 修改 Agent._should_respond() 实现分级响应 | P0 | - | 客服主动+被动，其他仅被动 | ✅ |
| - [x] 4.2.2 测试无@时只有客服响应 | P1 | 4.2.1 | 其他 Agent 不消耗 Token | ✅ |
| - [x] 4.2.3 测试被@时对应 Agent 响应 | P0 | 4.2.1 | 只有被@的 Agent 响应 | ✅ |

#### Step 3: AgentRegistry（1-2 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.3.1 创建 AgentRegistry 类 | P0 | - | 管理 Agent 生命周期 | ✅ |
| - [x] 4.3.2 实现待机池和延迟激活机制 | P0 | 4.3.1 | 首次被@时才创建实例 | ✅ |
| - [x] 4.3.3 实现客服常驻启动 | P0 | 4.3.2 | 应用启动时客服立即启动 | ✅ |
| - [x] 4.3.4 添加唤醒提示 "正在唤醒..." | P1 | 4.3.2 | 用户知道 Agent 在启动 | ✅ |
| - [ ] 4.3.5 实现空闲回收机制（可选） | P2 | 4.3.3 | 30分钟无活动停止 Docker | ⬜ |

#### Step 4: 消息队列（1 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.4.1 为 Agent 添加内部 asyncio.Queue | P0 | - | 每个 Agent 有消息队列 | ✅ |
| - [x] 4.4.2 实现 _message_worker() 串行处理 | P0 | 4.4.1 | 单 Agent 内消息按序处理 | ✅ |
| - [x] 4.4.3 验证 Agent 间并行执行 | P1 | 4.4.2 | 多个 Agent 可同时处理 | ✅ |

#### Step 5: 团队协作提示词（1 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.5.1 为 5 个角色编写完整 System Prompt | P0 | - | 包含角色描述、职责、节制规则 | ✅ |
| - [x] 4.5.2 实现 _build_team_table() 动态生成同事列表 | P0 | 4.5.1 | Agent 知道可以@谁 | ✅ |
| - [x] 4.5.3 注入对话节制规则到所有 Prompt | P0 | 4.5.1 | 避免循环@的自我约束 | ✅ |
| - [x] 4.5.4 从 ChatRoom 获取实时参与者列表 | P1 | 4.5.2 | 团队信息实时准确 | ✅ |

#### Step 6: 集成测试（2 天）
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 4.6.1 测试应用启动和多 Agent 注册 | P0 | 全部 | AgentRegistry 正确注册 6 个 Agent | ✅ |
| - [x] 4.6.2 验证客服常驻启动 | P1 | 全部 | 应用启动时客服立即启动 | ✅ |
| - [x] 4.6.3 验证延迟 Agent 进入待机池 | P1 | 全部 | 5 个 lazy-load Agent 在待机状态 | ✅ |
| - [x] 4.6.4 测试消息队列串行处理 | P1 | 全部 | Agent worker 正常启动运行 | ✅ |
| - [ ] 4.6.5 完整协作场景测试（需 LLM） | P2 | 全部 | 客服→项目经理→开发→汇报流程 | ⬜ |

### 里程碑 4 验收标准

- [x] 客服常驻启动，用户输入时主动响应
- [x] 其他角色延迟启动，首次被@时唤醒并显示提示
- [x] @特定 Agent，只有该 Agent 响应（客服除外）
- [x] 无@的消息只有客服响应，其他 Agent 不消耗 Token
- [ ] Agent 可以通过 sendMessage @其他 Agent（需 LLM 测试）
- [x] 每个 Agent 有独立的 Docker、独立的记忆存储
- [ ] 完整协作场景可运行（需 LLM 测试故障处理流程）
- [x] 循环@防护规则注入 System Prompt
- [x] Agent 内消息串行处理，Agent 间并行执行

### 里程碑 4 Demo 场景

#### Demo 1: 客服首响应
```
> 系统登录不上了
[客服] 收到，我来看一下...
       初步判断可能是数据库或服务器问题，我帮您转给专业同事处理。
       @运维 @开发-A 请帮忙排查登录故障

[系统] 正在唤醒 运维...
[系统] 正在唤醒 开发-A...

[运维] 我在检查服务器状态...
[开发-A] 我检查数据库连接...
```

#### Demo 2: 项目经理协调
```
> @项目经理 需要安排新功能开发

[项目经理] 收到，我来协调一下。
           @产品经理 请评估需求
           @开发-A @开发-B 评估技术方案

[产品经理] 需求已记录，建议分两个阶段实现...

[开发-A] 技术可行，我负责后端部分
[开发-B] 前端交给我，需要 3 天

[项目经理] @人类 已协调完成，预计本周五交付
```

#### Demo 3: 循环@自我节制
```
[客服] @开发-A 这个问题需要您处理
[开发-A] 收到，我来排查

[客服] [不再回复，等待开发-A 结果]

[开发-A] @人类 问题已修复，原因是...
```

#### Demo 4: 延迟启动体验
```
# 首次 @开发-A（开发-A 未启动）
> @开发-A 帮我看看代码
[系统] 正在唤醒 开发-A...
[开发-A] 来了，让我看看代码问题

# 第二次 @开发-A（已启动）
> @开发-A 还有个问题
[开发-A] [直接响应，无唤醒提示]
```

---

## 里程碑 5: Server 架构 + TUI + Wizard

**目标**: 实现 Server-Client 分离架构，提供 TUI 客户端和配置向导

**状态**: ✅ 完成 (Phase 5.1-5.6 全部完成)

**设计文档**:
- [UI 架构设计](../01-architecture/ui-architecture.md)
- [TUI 设计](../cli/tui_spec.md)
- [Wizard 设计](../03-wizard/wizard-design.md)

**已交付**:
- ✅ Server 主进程 (`minions server`) 支持 REST + WebSocket API
- ✅ 多客户端 Session 管理
- ✅ TlonClient SDK (WebSocket + REST)
- ✅ CLI 命令重构 (`minions server/tui/wizard`)
- ✅ Wizard 配置向导 (`minions wizard`)
  - 快速开始 (环境检查 → API 配置 → Agent 选择 → 创建配置)
  - Agent 管理 (查看/添加/修改/删除)
  - 环境诊断 (Python/Docker/磁盘/网络)
  - 测试验证 (LLM/Docker 连接测试)
  - 数据重置

**周期**: 预计 7-9 天

### 任务清单

#### Phase 5.1: Server 核心重构 (Day 1-2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.1.1 创建 Server 主进程架构 | P0 | - | `minions server` 可启动 | ✅ |
| - [x] 5.1.2 实现 REST API (aiohttp) | P0 | 5.1.1 | `/health`, `/history`, `/agents` 可用 | ✅ |
| - [x] 5.1.3 实现 WebSocket 服务 | P0 | 5.1.1 | `/ws` 支持实时通信 | ✅ |
| - [x] 5.1.4 Session 管理（多客户端） | P0 | 5.1.3 | 支持多个 TUI/Web 同时连接 | ✅ |
| - [ ] 5.1.5 Event 历史持久化 | P1 | 5.1.2 | Event 存储到 Server 端 | ⬜ |

#### Phase 5.2: Client SDK (Day 2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.2.1 创建 TlonClient SDK | P0 | 5.1.3 | WebSocket 连接管理 | ✅ |
| - [x] 5.2.2 消息/事件/命令封装 | P0 | 5.2.1 | 统一接口 | ✅ |
| - [x] 5.2.3 TUI 和 Server 共用 SDK | P0 | 5.2.2 | SDK 可复用 | ✅ |

#### Phase 5.3: Wizard 配置向导 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.3.1 环境诊断功能 | P0 | - | `wizard check` 检查系统 | ✅ |
| - [x] 5.3.2 快速初始化 (`wizard init`) | P0 | 5.3.1 | 交互式配置生成 | ✅ |
| - [x] 5.3.3 Agent 管理（添加/修改/删除） | P0 | 5.3.2 | 支持名字、职位、特长修改 | ✅ |
| - [x] 5.3.4 API 配置管理 | P1 | 5.3.2 | 修改 API Key、模型 | ✅ |
| - [x] 5.3.5 测试验证功能 | P1 | 5.3.4 | LLM/Docker 连通性测试 | ✅ |
| - [x] 5.3.6 重置功能 | P2 | 5.3.5 | 清理数据 | ✅ |

#### Phase 5.4: TUI 客户端 (Day 4-6)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.4.1 TUI 框架搭建 (Textual) | P0 | 5.2.3 | 基础界面布局 | ✅ |
| - [x] 5.4.2 ChatRoom 模式实现 | P0 | 5.4.1 | 聊天、历史、成员列表 | ✅ |
| - [x] 5.4.3 Event 监控模式 | P0 | 5.4.2 | 事件流、过滤、分页 | ✅ |
| - [x] 5.4.4 Agent 管理模式 | P0 | 5.4.3 | 状态查看、启停控制 | ✅ |
| - [x] 5.4.5 `/` 命令系统 | P0 | 5.4.2 | Vim 风格命令 | ✅ |
| - [x] 5.4.6 键盘快捷键 | P1 | 5.4.5 | 全局/模式快捷键 | ✅ |

#### Phase 5.5: CLI 命令重构 (Day 6-7)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.5.1 `minions server` 命令 | P0 | 5.1.x | 启动 Server 进程 | ✅ |
| - [x] 5.5.2 `minions tui` 命令 | P0 | 5.4.x | 启动 TUI 客户端 | ✅ |
| - [x] 5.5.3 `minions wizard` 命令 | P0 | 5.3.x | 启动配置向导 | ✅ |
| - [x] 5.5.4 `minions` 向后兼容 | P0 | 5.5.2 | 无参数启动嵌入式模式 | ✅ |
| - [x] 5.5.5 命令行参数解析 | P1 | 5.5.4 | `--host`, `--port` 等 | ✅ |

#### Phase 5.6: 集成与测试 (Day 7-9)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [x] 5.6.1 Server + TUI 端到端测试 | P0 | 全部 | 完整流程可用 | ✅ |
| - [x] 5.6.2 多客户端同时连接测试 | P1 | 5.6.1 | TUI + Web 同时在线 | ✅ |
| - [x] 5.6.3 断线重连测试 | P1 | 5.6.2 | SDK 已实现断线重连 | ✅ |
| - [ ] 5.6.4 性能测试 | P2 | 5.6.3 | 1000 消息不卡顿 | ⬜ |

### 里程碑 5 验收标准

- [x] `minions server` 启动长期运行的核心服务
- [x] `minions tui` 连接 Server，进入 ChatRoom 模式聊天
- [x] `minions wizard` 完成环境检查、配置初始化
- [x] TUI 三种模式可用：ChatRoom / Event / Agent
- [x] `/` 命令系统完整（/mode, /filter, /wake 等）
- [x] Wizard 支持 Agent 名字、职位、特长的修改
- [x] 向后兼容：`minions` 启动嵌入式模式
- [x] 多客户端可同时连接（多个 TUI/WebSocket 客户端）

---

## 里程碑 6: Web 界面 + Telegram Bot

**目标**: 实现 Web 客户端和 Telegram Bot，完善多界面支持

**设计文档**:
- [Web 设计](../web/web-design.md)

**周期**: 预计 8-10 天

### 任务清单

#### Phase 6.1: Web 前端基础 (Day 1-2)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.1.1 React + Vite + TypeScript 项目搭建 | P0 | - | 开发服务器可运行 | ⬜ |
| - [ ] 6.1.2 Tailwind CSS + Radix UI 配置 | P0 | 6.1.1 | 组件库可用 | ⬜ |
| - [ ] 6.1.3 Zustand Store 设计 | P0 | 6.1.2 | 状态管理架构 | ⬜ |
| - [ ] 6.1.4 WebSocket 客户端封装 | P0 | 6.1.3 | 与 Server 通信 | ⬜ |

#### Phase 6.2: Web ChatRoom 模式 (Day 2-3)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.2.1 聊天界面布局 | P0 | 6.1.4 | 侧边栏 + 消息区 + 输入框 | ⬜ |
| - [ ] 6.2.2 消息组件（人类/Agent/系统） | P0 | 6.2.1 | 不同样式渲染 | ⬜ |
| - [ ] 6.2.3 @提及补全 | P1 | 6.2.2 | Popover 成员列表 | ⬜ |
| - [ ] 6.2.4 代码块高亮 | P1 | 6.2.2 | Prism.js 语法高亮 | ⬜ |
| - [ ] 6.2.5 工具执行折叠 | P1 | 6.2.2 | 展开/收起详情 | ⬜ |

#### Phase 6.3: Web Event 模式 (Day 3-4)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.3.1 Event 列表组件 | P0 | 6.1.4 | 时间线布局 | ⬜ |
| - [ ] 6.3.2 过滤器面板 | P0 | 6.3.1 | Agent/类型/时间筛选 | ⬜ |
| - [ ] 6.3.3 Event 详情展开 | P1 | 6.3.2 | JSON 格式化展示 | ⬜ |
| - [ ] 6.3.4 分页加载 | P1 | 6.3.1 | 滚动加载更多 | ⬜ |

#### Phase 6.4: Web Agent 模式 (Day 4-5)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.4.1 Agent 列表视图 | P0 | 6.1.4 | 表格/卡片布局 | ⬜ |
| - [ ] 6.4.2 Agent 详情视图 | P0 | 6.4.1 | 信息/资源/统计 | ⬜ |
| - [ ] 6.4.3 实时资源图表 | P1 | 6.4.2 | Recharts 实时更新 | ⬜ |
| - [ ] 6.4.4 启停操作按钮 | P0 | 6.4.1 | 唤醒/休眠/重启 | ⬜ |
| - [ ] 6.4.5 批量操作 | P1 | 6.4.4 | 全部唤醒/休眠 | ⬜ |

#### Phase 6.5: Web 响应式与优化 (Day 5-6)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.5.1 移动端适配 | P1 | 6.4.x | 底部导航/抽屉菜单 | ⬜ |
| - [ ] 6.5.2 暗黑/亮色主题 | P2 | 6.5.1 | 主题切换 | ⬜ |
| - [ ] 6.5.3 前端构建部署 | P1 | 6.5.2 | 静态文件输出 | ⬜ |
| - [ ] 6.5.4 Server 集成静态文件 | P1 | 6.5.3 | `/` 返回 Web UI | ⬜ |

#### Phase 6.6: Telegram Bot (Day 6-8)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.6.1 python-telegram-bot 集成 | P0 | - | Bot 基础框架 | ⬜ |
| - [ ] 6.6.2 共用 TlonClient SDK | P0 | 6.6.1 | 与 TUI 共用 SDK | ⬜ |
| - [ ] 6.6.3 模式切换内联键盘 | P0 | 6.6.2 | [💬 Chat] [📡 Event] [🎛️ Agent] | ⬜ |
| - [ ] 6.6.4 ChatRoom 模式实现 | P0 | 6.6.3 | 收发信息 | ⬜ |
| - [ ] 6.6.5 Event 模式（分页列表） | P0 | 6.6.4 | 过滤/详情 | ⬜ |
| - [ ] 6.6.6 Agent 模式（列表+操作） | P0 | 6.6.5 | 状态查看/启停 | ⬜ |
| - [ ] 6.6.7 用户会话管理 | P1 | 6.6.6 | 多用户支持 | ⬜ |
| - [ ] 6.6.8 认证机制 | P1 | 6.6.7 | `/auth <token>` | ⬜ |

#### Phase 6.7: 集成测试 (Day 8-10)
| 任务 | 优先级 | 依赖 | 验收标准 | 状态 |
|------|--------|------|----------|------|
| - [ ] 6.7.1 Web + Server 端到端测试 | P0 | 全部 | 完整流程 | ⬜ |
| - [ ] 6.7.2 Telegram + Server 测试 | P0 | 全部 | Bot 响应正常 | ⬜ |
| - [ ] 6.7.3 三端同时在线测试 | P1 | 6.7.2 | TUI/Web/Telegram 消息同步 | ⬜ |
| - [ ] 6.7.4 部署文档 | P1 | 6.7.3 | Docker Compose 配置 | ⬜ |

### 里程碑 6 验收标准

- [ ] Web 界面可访问，`/chat` 模式可聊天
- [ ] Web 三种模式完整：ChatRoom / Event / Agent
- [ ] Web 支持响应式，移动端可用
- [ ] Telegram Bot 可接收/发送消息
- [ ] Telegram 三种模式可用（内联键盘切换）
- [ ] TUI + Web + Telegram 可同时连接
- [ ] 消息在三端实时同步

### 里程碑 6 Demo 场景

**三端同步测试：**
```
[Telegram]  User: @客服 帮我看看服务器
[TUI]       显示: [客服] 收到...
[Web]       显示: [客服] 收到...

[TUI]       User: 直接发消息
[Web]       显示: 👤 人类: 直接发消息
[Telegram]  收到: 👤 人类: 直接发消息

[Agent 响应在所有端同步显示]
```

---

## 依赖关系图

```
里程碑 1: 单 Agent + CLI
├── 1.1 项目骨架
├── 1.2 核心层 (Message, EventBus, ChatRoom)
├── 1.3 基础组件 (LLM, Docker, Tool)
├── 1.4 Agent 主类
└── 1.5 CLI Adapter
    │
    ▼
里程碑 2: Skills 系统（Agent Skills 标准）
├── 2.1 Skill 核心机制
│   ├── SkillManager (Discovery/Activation/Execution)
│   └── SKILL.md 解析器
├── 2.2 Skill 集成到 Agent ← 依赖 1.4
├── 2.3 Skill 脚本执行 ← 依赖 1.3.2 (Docker)
├── 2.4 基础 Skills 开发 ← 依赖 2.3
├── 2.5 Skill 验证与文档
└── 2.6 集成测试
    │
    ▼
里程碑 3: Memory 系统
├── 3.1 Memory 核心机制
├── 3.2 记忆注入与上下文
├── 3.3 记忆摘要与整理
└── 3.4 集成测试
    │
    ▼
里程碑 4: 多 Agent 系统
├── 4.1 配置和启动 ← 依赖 1.4, 3.4
├── 4.2 Agent 间消息 ← 依赖 1.2
├── 4.3 角色差异化
├── 4.4 多 Agent 协调
└── 4.5 集成测试
    │
    ▼
里程碑 5: 拓展接口 + Telegram
├── 5.1 适配器抽象 ← 依赖 1.5
├── 5.2 Telegram Adapter ← 依赖 5.1, 4.2
├── 5.3 多适配器支持
└── 5.4 集成测试
```

---

## 开发顺序建议

### 整体顺序
按里程碑顺序开发：1 → 2 → 3 → 4

### 里程碑内顺序
**里程碑 1 关键路径：**
```
Message → EventBus → LLMClient → DockerRuntime → Tool → Agent → CLI → 集成
```

**里程碑 2 关键路径（Skills 系统）：**
```
SkillManager → SKILL.md 解析 → Discovery → Activation → loadSkillResource → 集成
```

**里程碑 4 关键路径（新增）：**
```
SkillManager → SKILL.md 解析 → Discovery → Activation → 脚本执行 → 集成
```

### 可并行的任务
- 前端/CLI 美化 与 后端逻辑开发
- Skill 文档编写 与 Skill 代码开发
- 测试用例编写 与 功能开发

---

## 技术风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|----------|
| Docker 权限问题 | 高 | 提前测试 Docker SDK，准备 fallback 方案 |
| LLM API 不稳定 | 中 | 重试机制 + 本地模型备选 |
| Telegram Bot 被封 | 低 | 文档中提供 Webhook 替代方案 |
| Agent 循环@ | 中 | 限制思考轮次，检测循环并中断 |
| 消息顺序混乱 | 中 | Event Bus 保证 FIFO，必要时加序列号 |
| Skill 格式兼容 | 低 | 严格遵循 Agent Skills 规范，提供验证工具 |
| Skill 脚本安全 | 中 | 在 Docker 容器中执行，限制宿主机访问 |

---

## 目录结构演进

```
里程碑 1:
minions/
├── minions/
│   ├── core/          # Message, EventBus, ChatRoom
│   ├── adapters/      # CLI
│   ├── agents/        # Agent, LLM, Docker, Tool
│   └── main.py
├── config/
│   └── agents.yaml    # 单 Agent 配置
└── data/              # 消息历史

里程碑 2:
├── config/
│   └── agents.yaml    # 多 Agent 配置
├── data/
│   ├── chat_history/
│   └── agents/        # 各 Agent 记忆

里程碑 3:
├── minions/adapters/
│   ├── telegram_adapter.py
│   └── base.py        # 完善抽象

里程碑 4 (Agent Skills 标准):
├── skills/            # Skills 目录
│   ├── file-operations/
│   │   ├── SKILL.md              # 必需：元数据和指令
│   │   ├── scripts/              # 可选：可执行脚本
│   │   │   ├── read_file.py
│   │   │   └── write_file.py
│   │   └── references/           # 可选：参考文档
│   │       └── FILE_API.md
│   ├── git-operations/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── clone.py
│   │       └── commit.py
│   └── web-search/
│       ├── SKILL.md
│       └── scripts/
├── minions/agents/
│   ├── ...
│   └── skill_manager.py    # 新增：Skill 管理器
└── minions/tools/
    ├── ...
    └── read_skill_file.py  # 新增：读取 Skill 文件工具
```

---

## 任务总览

### 里程碑 1 任务统计
- 总任务数: 22
- 已完成: 22 ✅
- 进行中: 0
- 待开始: 0

### 里程碑 2 任务统计（Skills 系统）
- 总任务数: 20
- 已完成: 20 ✅
- 进行中: 0
- 待开始: 0

### 里程碑 3 任务统计（Memory 系统）
- 总任务数: 14
- 已完成: 14 ✅
- 进行中: 0
- 待开始: 0

### 里程碑 4 任务统计（多 Agent 系统）
- 总任务数: 15
- 已完成: 14 ✅
- 进行中: 0
- 待开始: 1 (空闲回收机制)

### 里程碑 5 任务统计（Server + TUI + Wizard）
- 总任务数: 22
- 已完成: 0
- 进行中: 0
- 待开始: 22

### 里程碑 6 任务统计（Web + Telegram）
- 总任务数: 26
- 已完成: 0
- 进行中: 0
- 待开始: 26

---

## Agent Skills 参考资源

- [Agent Skills 规范](https://github.com/agentskills/agentskills/tree/main/docs)
- [Skill 示例](https://github.com/anthropics/skills)
- [skills-ref 验证工具](https://github.com/agentskills/agentskills/tree/main/skills-ref)
- [PEP 723 - 脚本内联依赖](https://peps.python.org/pep-0723/)

---

## 下一步

### 当前状态
- 里程碑 1 (单 Agent + CLI): ✅ 完成
- 里程碑 2 (Skills 系统): ✅ 完成
- 里程碑 3 (Memory 系统): ✅ 完成
- 里程碑 4 (多 Agent 系统): ✅ 基本完成

### 新架构里程碑（推荐顺序）

**里程碑 5: Server + TUI + Wizard**
- 核心架构重构，Server-Client 分离
- TUI 三种模式（ChatRoom/Event/Agent）
- Wizard 配置向导

**里程碑 6: Web + Telegram**
- React Web 界面
- Telegram Bot
- 三端同时在线支持

### 设计文档索引
| 文档 | 内容 |
|------|------|
| [ui-architecture.md](../01-architecture/ui-architecture.md) | 多界面架构设计 |
| [tui_spec.md](../cli/tui_spec.md) | TUI 客户端（三种模式） |
| [web-design.md](../web/web-design.md) | Web 客户端（React） |
| [wizard-design.md](../03-wizard/wizard-design.md) | 配置向导 |

**建议开始**: 里程碑 5 的 Phase 5.1 (Server 核心重构)
- 5.1.1 创建 Server 主进程架构
- 5.1.2 实现 REST API (FastAPI)
- 5.1.3 实现 WebSocket 服务

计划是否可行？有任何需要调整的地方吗？
