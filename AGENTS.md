# Tlon 开发指南

> 一个由观念构建的、正在入侵现实的宇宙

---

## 产品风格

Tlon 的产品风格以**博尔赫斯文学意象**为主。

详细的品牌叙事、术语体系和文案规范，参见：
- [docs/marketing/brand-narrative-v2.md](docs/marketing/brand-narrative-v2.md)

核心原则：
- **诗意 + 工程精确**：每个概念既有文学隐喻，又有具体的技术实现
- **渐进式揭示**：用户在使用过程中层层发现宇宙的完整图景
- **双层文案**：品牌层（诗意）+ 实现层（精确）

关键术语（博尔赫斯宇宙）：
| 概念 | 命名 | 出处 |
|-----|------|------|
| 产品 | Tlon / 特隆 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| Chatroom | 代表大会 / Congress | 《代表大会》 |
| 记忆系统 | Shakespeare / 莎士比亚 | 《与莎士比亚的记忆》 |
| Skill 系统 | 巴别图书馆 / Library of Babel | 《巴别图书馆》 |
| Git 工作流 | 花园 / Garden | 《小径分叉的花园》 |
| 自举计划 | 环形废墟 / Circular Ruins | 《环形废墟》 |
| Agent 管理 | Mirror / 镜 | 《镜子与面具》 |

---

## 项目概述

Tlon（特隆）是一个多智能体协作系统，模拟软件工程团队的工作方式。系统通过 Docker 容器运行多个 AI Agent，每个 Agent 拥有独立的身份、记忆和技能，通过聊天室（Congress/代表大会）协作完成用户的软件项目需求。

### 核心概念（博尔赫斯宇宙）

| 概念 | 命名 | 出处 |
|-----|------|------|
| 产品 | Tlon / 特隆 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| Chatroom | 代表大会 / Congress | 《代表大会》 |
| 记忆系统 | Shakespeare / 莎士比亚 | 《与莎士比亚的记忆》 |
| Skill 系统 | 巴别图书馆 / Library of Babel | 《巴别图书馆》 |
| Git 工作流 | 花园 / Garden | 《小径分叉的花园》 |
| 自举计划 | 环形废墟 / Circular Ruins | 《环形废墟》 |
| Agent 管理 | Mirror / 镜 | 《镜子与面具》 |

### 主要功能

- **多 Agent 协作**: 客服、项目经理、开发、运维等角色各司其职
- **长时记忆**: Agent 记住用户偏好、项目历史
- **技能系统**: 动态加载技能（Git、文件操作、前端设计等）
- **Docker 隔离**: 每个 Agent 独立容器环境
- **任务管理**: Agent 可创建和管理任务列表

---

## 技术栈

- **语言**: Python 3.11+
- **包管理**: uv (现代化 Python 包管理器)
- **构建工具**: Hatchling (PEP 517)
- **异步框架**: asyncio + aiohttp
- **LLM 集成**: OpenAI 兼容 API
- **容器化**: Docker Python SDK
- **配置**: YAML + python-dotenv
- **日志**: structlog + rich

---

## 构建与运行

### 环境要求

- Python 3.11+
- Docker（运行 Agent 容器）
- OpenAI API Key 或其他兼容的 LLM 服务

### 安装

```bash
# 使用 uv 安装依赖
uv sync
```

### 配置

创建 `.env` 文件：

```bash
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY
```

### 运行方式

```bash
# 方式一：服务端 + TUI 客户端（推荐）
uv run tlon server    # 终端 1：启动服务
uv run tlon tui       # 终端 2：启动 TUI

# 方式二：配置向导
uv run tlon wizard

# 方式三：查看帮助
uv run tlon --help
```

---

## 测试

### 运行测试

```bash
# 运行所有测试
uv run pytest

# 运行特定模块测试
uv run pytest tests/config/
uv run pytest tests/wizard/

# 带覆盖率
uv run pytest --cov=tlon --cov-report=html
```

---

## 代码风格

项目使用以下工具保证代码质量：

### Black（代码格式化）

```bash
# 格式化代码
black src/ tests/

# 检查格式
black --check src/ tests/
```

配置（`pyproject.toml`）：

```toml
[tool.black]
line-length = 100
target-version = ["py311"]
```

### Ruff（代码检查）

```bash
# 运行检查
ruff check src/ tests/

# 自动修复
ruff check --fix src/ tests/
```

配置（`pyproject.toml`）：

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = ["E", "W", "F", "I", "N", "UP", "B", "C4", "SIM"]
ignore = ["E501"]
```

### mypy（类型检查）

```bash
mypy src/tlon
```

配置要求严格类型检查：
- `disallow_untyped_defs = true`
- `disallow_incomplete_defs = true`
- `no_implicit_optional = true`

---

## 导入路径速查

### 基础层

```python
from tlon.base.config import ConfigLoader, ConfigSaver
from tlon.base.config.models import AgentConfig, PositionConfig
from tlon.base.events import Event, EventType, EventBus
from tlon.base.logger import get_logger
from tlon.base.settings import settings
```

### 组织层

```python
from tlon.organization.messaging import Congress, Message
from tlon.organization.positions import PositionRegistry, Position
```

### Agent 层

```python
from tlon.agents import Agent, AgentRegistry
from tlon.agents.llm_client import LLMClient
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.prompt_builder import SystemPromptBuilder
```

### Sandbox 层

```python
from tlon.sandbox.docker_runtime import DockerRuntime
```

### 子系统层

```python
from tlon.subsystem.memory import MemoryManager
from tlon.subsystem.skills import SkillManager
from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.tools.tasks import TaskManager, Task
```

---

## 开发流程

### 阶段一：方案讨论（⚠️ 禁止修改文件）

1. 首先与用户讨论设计方案
2. 使用提问、建议、对比方案等方式深入理解需求
3. **在用户明确批准设计方案之前，不得修改任何文件**

### 阶段二：文档化（✅ 可以写文档）

1. 用户批准设计方案后
2. 将方案写入 `docs/` 目录下的适当位置
3. 可以是新的文档，或更新现有文档

### 阶段三：等待实施指令（⚠️ 禁止写代码）

1. 在**用户明确说"开始实施"之前**，不得编写代码
2. 可以继续讨论、澄清、完善文档

### 阶段四：实施（✅ 开始编码）

1. 用户说"开始实施"之后
2. **先撰写开发路线图**：创建 `docs/developer/roadmap.md`
   - 将设计方案分解为多个小 task
   - 每个 task 应该是可独立实现、可测试的单元
   - 标注优先级和依赖关系
3. **按 roadmap 逐步实施**：
   - 每次完成一个 task
   - 完成后更新 roadmap 状态
   - 如有需要，与用户确认下一步

---

## 流程示例

```
用户：我想添加一个新功能 X

我：讨论方案 A、B、C 的优劣...
     你觉得哪个方向更好？
     
用户：选方案 B，但要做这些调整...

我：确认一下，方案 B 的调整后是：1...2...3...
     对吗？
     
用户：对的，批准这个方案

[我：写文档 docs/developer/features/x-design.md]

我：方案已文档化，等待实施指令

用户：开始实施

[我：创建 docs/developer/roadmap.md]
[我：按计划逐步实施每个 task]
```

---

## 注意事项

- **禁止提前实施**：不要在讨论阶段就"顺手"改代码
- **文档先行**：所有设计必须落实在文档中
- **小步快跑**：Roadmap 的 task 要足够小，方便回溯和调整
- **及时同步**：完成每个 task 后更新进度

---

## 添加新功能

### 添加新 Tool

1. 在 `src/tlon/subsystem/tools/{category}/` 创建新文件，继承 `Tool` 基类
2. 实现 `name`, `description`, `parameters`, `execute()` 方法
3. 在 `Agent._register_tools()` 中注册

```python
# src/tlon/subsystem/tools/my_category/my_tool.py
from ..base import Tool, ToolResult

class MyTool(Tool):
    name = "myTool"
    description = "工具描述"
    parameters = {...}
    
    async def execute(self, **kwargs) -> ToolResult:
        # 实现逻辑
        return ToolResult.success(data=...)
```

### 添加新 Skill

1. 创建 `skills/{skill-name}/` 目录
2. 编写 `SKILL.md`（YAML frontmatter + Markdown）
3. 添加 `scripts/` 脚本（可选）
4. 在 Agent 配置中启用

### 添加新 Agent

1. 创建 `config/agents/{agent-id}.yaml`
2. 指定 `position_id` 关联岗位
3. 配置技能、LLM、Docker 参数
4. 运行 `tlon wizard` 可交互式创建

---

## 安全考虑

### Docker 隔离

- 每个 Agent 独立 Docker 容器
- 容器资源限制（内存、CPU）
- 文件系统隔离

### API 密钥管理

- 使用 `.env` 文件存储敏感信息
- 绝不将 `.env` 提交到版本控制
- 支持环境变量覆盖

### 技能安全

- 路径遍历攻击防护（`load_resource` 检查）
- Skill 只在需要时激活，用完可释放

---

## 调试技巧

### 启用调试日志

```bash
tlon server --debug
# 或
DEBUG=true tlon server
```

### 查看 Agent 记忆

记忆存储在：`data/agents/{agent_id}/memory/`

- `config.json` - 记忆配置
- `working/interactions.jsonl` - 工作记忆
- `episodic/summaries.jsonl` - 会话摘要
- `episodic/facts.jsonl` - 关键事实

### 查看聊天历史

`data/chat_history/congress.jsonl`

### Docker 容器调试

```bash
# 查看运行中的 Agent 容器
docker ps

# 进入容器
docker exec -it tlon-agent-{agent_id} bash
```

---

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/developer/01-architecture/overview.md` | 架构设计文档 |
| `docs/developer/01-architecture/memory.md` | Memory 系统详细设计 |
| `docs/developer/01-architecture/multi-agent.md` | 多 Agent 协作系统设计 |
| `docs/developer/01-architecture/ui-architecture.md` | UI 架构设计 |
| `docs/developer/03-wizard/` | 配置向导设计 |
| `docs/developer/04-skills/` | 技能系统设计 |
| `docs/developer/05-tasks/` | 任务系统设计 |
| `docs/developer/06-testing/` | 测试体系 |
| `docs/user/` | 用户指南 |
| `docs/marketing/brand-narrative-v2.md` | 品牌叙事 |

---

## 相关链接

- **Agent Skills 标准**: https://github.com/agentskills/agentskills
- **OpenAI API**: https://platform.openai.com/docs
- **Docker SDK**: https://docker-py.readthedocs.io/

---

*最后更新：2026-03-15*
