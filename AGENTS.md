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
- **构建工具**: Hatchling (PEP 517)
- **包管理**: uv (现代化 Python 包管理器)
- **异步框架**: asyncio + aiohttp
- **LLM 集成**: OpenAI 兼容 API
- **容器化**: Docker Python SDK
- **配置**: YAML + python-dotenv
- **日志**: structlog + rich

### 关键依赖

```toml
# 核心
pydantic>=2.0.0
PyYAML>=6.0
python-dotenv>=1.0.0
aiofiles>=23.0.0
jinja2>=3.1.0

# LLM
openai>=1.0.0

# Docker
docker>=6.0.0

# Server
aiohttp>=3.9.0

# Telegram
python-telegram-bot>=20.0

# CLI TUI
prompt-toolkit>=3.0.52
rich>=14.3.3
```

---

## 项目结构

```
tlon/
├── src/tlon/                 # 主源代码
│   ├── __init__.py
│   ├── __main__.py           # 模块入口
│   ├── settings.py           # 全局配置
│   ├── logger.py             # 日志配置
│   │
│   ├── adapters/             # 交互层（预留）
│   │   ├── base.py
│   │   └── __init__.py
│   │
│   ├── agents/               # Agent 核心实现
│   │   ├── agent.py          # Agent 类（核心）
│   │   ├── agent_registry.py # Agent 注册表
│   │   ├── config.py         # Agent 配置
│   │   ├── docker_runtime.py # Docker 运行时
│   │   ├── llm_client.py     # LLM 客户端
│   │   ├── prompt_builder.py # 系统提示构建
│   │   └── tool_executor.py  # 工具执行器
│   │
│   ├── cli/                  # 命令行界面
│   │   ├── main.py           # CLI 主入口
│   │   ├── tui.py            # TUI 实现（富文本）
│   │   ├── tui_simple.py     # 简化 TUI（主要实现）
│   │   └── wizard.py         # 配置向导
│   │
│   ├── clients/              # 客户端 SDK
│   │   └── sdk.py
│   │
│   ├── config/               # 配置管理
│   │   ├── loader.py         # 配置加载器
│   │   ├── models.py         # 配置模型
│   │   ├── prompts.py        # 提示模板管理
│   │   ├── saver.py          # 配置保存
│   │   └── validator.py      # 配置验证
│   │
│   ├── memory/               # 记忆系统（Shakespeare）
│   │   ├── manager.py        # 记忆管理器
│   │   ├── models.py         # 记忆模型
│   │   ├── working_store.py  # 工作记忆存储
│   │   ├── episodic_store.py # 情景记忆存储
│   │   ├── summarizer.py     # 摘要生成器
│   │   └── base.py           # 基础定义
│   │
│   ├── messaging/            # 消息系统
│   │   ├── congress.py       # 聊天室（代表大会）
│   │   ├── event_bus.py      # 事件总线
│   │   └── __init__.py
│   │
│   ├── positions/            # 岗位系统
│   │   ├── models.py         # 岗位模型
│   │   ├── registry.py       # 岗位注册表
│   │   └── prompt_renderer.py
│   │
│   ├── protocol/             # 协议定义
│   │   ├── message.py        # 消息格式
│   │   └── event.py          # 事件格式
│   │
│   ├── server/               # 服务端
│   │   ├── app.py            # 服务端应用
│   │   ├── core/server.py    # 核心服务
│   │   └── api/              # API 层
│   │       ├── rest.py       # REST API
│   │       └── websocket.py  # WebSocket API
│   │
│   ├── skills/               # 技能系统（Library of Babel）
│   │   ├── manager.py        # 技能管理器
│   │   ├── models.py         # 技能模型
│   │   └── validator.py      # 技能验证
│   │
│   ├── tasks/                # 任务系统
│   │   ├── manager.py
│   │   └── models.py
│   │
│   ├── tools/                # 工具集
│   │   ├── base.py           # 工具基类
│   │   ├── execute_bash.py   # Bash 执行
│   │   ├── send_message.py   # 消息发送
│   │   ├── activate_skill.py # 技能激活
│   │   ├── remember_fact.py  # 记忆记录
│   │   ├── search_memories.py
│   │   ├── create_task.py    # 任务创建
│   │   └── ...
│   │
│   └── wizard/               # 配置向导（Mirror）
│       ├── core.py           # 向导核心
│       ├── agent_store.py    # Agent 存储
│       ├── position_store.py # 岗位存储
│       ├── diagnostics.py    # 诊断工具
│       ├── templates.py      # 模板
│       └── menus/            # 菜单
│
├── config/                   # 配置文件
│   ├── agents/               # Agent 配置（YAML）
│   │   ├── lin-zhiyao.yaml   # 林知遥
│   │   ├── su-wanqing.yaml   # 苏晚晴
│   │   └── ...
│   ├── positions/            # 岗位配置（YAML）
│   │   ├── developer.yaml
│   │   ├── project_manager.yaml
│   │   └── ...
│   └── prompt_templates.yaml # 提示模板
│
├── skills/                   # 技能定义（Library）
│   ├── git-operations/       # Git 操作
│   │   ├── SKILL.md
│   │   └── scripts/
│   ├── file-operations/      # 文件操作
│   ├── csv-pipeline/         # CSV 处理
│   ├── frontend-design/      # 前端设计
│   └── plan-i/               # 计划生成
│
├── data/                     # 数据存储
│   └── agents/
│       └── {agent_id}/
│           └── memory/       # Agent 记忆
│
├── tests/                    # 测试
│   ├── config/               # 配置测试
│   ├── wizard/               # 向导测试
│   ├── tasks/                # 任务测试
│   └── test_skills/          # 技能测试
│
└── docs/                     # 文档
    ├── developer/            # 开发文档
    ├── user/                 # 用户文档
    └── marketing/            # 品牌文档
```

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

# 或使用 pip
pip install -e ".[dev]"
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
tlon server    # 终端 1：启动服务
tlon tui       # 终端 2：启动 TUI

# 方式二：配置向导
tlon wizard

# 方式三：查看帮助
tlon --help
```

---

## 测试

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定模块测试
pytest tests/config/
pytest tests/wizard/

# 带覆盖率
pytest --cov=tlon --cov-report=html
```

### 测试配置

在 `pyproject.toml` 中配置：

```toml
[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --tb=short"
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

## 架构设计

### 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│                         交互层 (Adapters)                    │
│              CLI │ TUI │ WebSocket │ Telegram               │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                          核心层 (Core)                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Event Bus  │  │   Congress   │  │  Message Protocol   │ │
│  │  事件总线   │  │   聊天室     │  │    消息协议         │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                         Agent 层                             │
│  ┌─────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────┐ │
│  │ Agent   │ │ LLM Client │ │Tool Executor│ │Docker Runtime│ │
│  │ 智能体  │ │ LLM客户端  │ │  工具执行   │ │ Docker运行时 │ │
│  └────┬────┘ └────────────┘ └────────────┘ └─────────────┘ │
│       │                                                     │
│  ┌────┴────┐ ┌─────────────────┐ ┌──────────────────────┐ │
│  │ Skills  │ │  Memory Manager │ │    Task Manager      │ │
│  │ 技能系统 │ │    记忆管理     │ │     任务管理         │ │
│  └─────────┘ └─────────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 数据流向

```
用户输入 → CLI/TUI → Event Bus → Congress → Agent → LLM
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
               sendMessage              executeBash               activateSkill
                    │                         │                         │
                    └─────────────────────────┴─────────────────────────┘
                                              │
                                              ▼
                                         返回结果
                                              │
用户输出 ← CLI/TUI ← Event Bus ← Congress ← Agent
```

### Agent 生命周期

1. **初始化**: 加载配置 → 启动 Docker → 加载记忆 → 发现技能
2. **运行**: 订阅事件 → 处理消息队列（串行）→ 调用 LLM → 执行工具
3. **关闭**: 停止队列 → 保存记忆 → 停止 Docker

---

## 配置系统

### 岗位配置（Position）

`config/positions/{position_id}.yaml`:

```yaml
position:
  id: "developer"
  type: "developer"
  title: "开发工程师"
  department: "技术部"
  jd: |
    负责代码编写、Bug 修复和技术方案实现。
  proactive: false           # 是否主动响应
  contact_when: "有技术问题时" # 何时联系
  rules:                     # 行为规则
    - "只在你被@时响应"
  default_skills:
    - "git-operations"
```

### Agent 配置

`config/agents/{agent_id}.yaml`:

```yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"
  personality: "技术能力强、注重细节"
  position_id: "developer"
  skills:
    - "git-operations"
    - "file-operations"
  llm:
    model_name: "kimi-k2.5"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

### 技能定义

`skills/{skill-name}/SKILL.md`:

```yaml
---
name: git-operations
description: Perform Git version control operations
metadata:
  author: Tlon Team
  version: "1.0.0"
  tags: ["git", "vcs"]
---

# Git Operations

详细使用说明...
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

1. 在 `src/tlon/tools/` 创建新文件，继承 `Tool` 基类
2. 实现 `name`, `description`, `parameters`, `execute()` 方法
3. 在 `Agent._register_tools()` 中注册

```python
# src/tlon/tools/my_tool.py
from .base import Tool, ToolResult

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
| `docs/developer/03-wizard/` | 配置向导设计 |
| `docs/developer/04-skills/` | 技能系统设计 |
| `docs/developer/06-testing/` | 测试体系 |
| `docs/user/` | 用户指南 |
| `docs/marketing/brand-narrative-v2.md` | 品牌叙事 |

---

## 相关链接

- **Agent Skills 标准**: https://github.com/agentskills/agentskills
- **OpenAI API**: https://platform.openai.com/docs
- **Docker SDK**: https://docker-py.readthedocs.io/

---

*最后更新：2026-03-09*
