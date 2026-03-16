# 文档混乱与冲突梳理报告

> **审查日期**: 2026-03-16  
> **审查范围**: 全项目 Markdown 文档  
> **审查状态**: 🔍 已完成  
> **风险等级**: 🟡 中（文档债务累积，影响开发效率）

---

## 1. 执行摘要

通过对项目 50+ 个 Markdown 文件的系统性审查，发现 **7 大类**、**20+ 处** 文档冲突和混乱。主要问题包括：

| 类别 | 数量 | 严重程度 |
|------|------|---------|
| 名称/术语不一致 | 5 | 高 |
| 命令体系混乱 | 4 | 高 |
| 架构文档过时 | 3 | 中 |
| 路径/配置不一致 | 4 | 中 |
| 状态标记混乱 | 3 | 低 |
| 重复/冗余文档 | 3 | 低 |
| 缺失文档 | 2 | 中 |

---

## 2. 详细问题清单

### 2.1 名称/术语不一致 🔴

#### 问题 1: 项目名称混乱

| 文档位置 | 使用的名称 |
|----------|-----------|
| `README.md` | Tlon |
| `docs/README.md` | Minions |
| `docs/developer/config/config_architecture.md` | Minions |
| `pyproject.toml` | tlon |
| `AGENTS.md` | Tlon |

**冲突描述**: 项目到底叫 "Tlon" 还是 "Minions"？
- README 和 AGENTS 使用 **Tlon**（特隆）
- docs/ 下的文档大量使用 **Minions**
- Python 包名是 **tlon**

**建议**: 统一使用 **Tlon** 作为产品名称，**tlon** 作为 Python 包名，废弃 Minions。

---

#### 问题 2: Agent 配置字段不一致

| 文档 | 配置字段 |
|------|---------|
| `docs/developer/config/config_design.md` | `position_id` |
| `docs/developer/config/config_architecture.md` | `position_id` |
| `docs/user/engineering/quickstart.md` | `position` (无 `_id`) |

**quickstart.md 中的配置**:
```yaml
agents:
  - id: "dev-001"
    name: "陈代码"
    position: "developer"  # ❌ 应该是 position_id
```

**实际代码中的配置** (`config/agents/*.yaml`):
```yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"
  position_id: "developer"  # ✅ 正确
```

---

#### 问题 3: LLM 配置字段不一致

| 文档 | 字段名 |
|------|--------|
| `config_architecture.md` | `model`, `temperature`, `max_tokens` |
| `config_design.md` | `model`, `temperature`, `max_tokens` |
| 实际 YAML 配置 | `model_name`, `temperature` |
| `AGENTS.md` | 未明确说明 |

**config_architecture.md 中的定义**:
```python
@dataclass
class LLMConfig:
    model: str = "gpt-4o-mini"  # ❌ 实际是 model_name
    temperature: float = 0.7
    max_tokens: int = 4000  # ❌ 实际可能不存在
```

**实际代码** (`src/tlon/base/config/models.py`):
```python
@dataclass 
class LLMConfig:
    base_url: str = ""
    auth_token: str = ""
    model_name: str = "gpt-4o-mini"  # ✅ 实际是 model_name
    temperature: float = 0.7
    max_tokens: int = 4000
```

---

#### 问题 4: Docker 配置字段不一致

| 文档 | 字段名 |
|------|--------|
| `config_architecture.md` | `image`, `work_dir`, `timeout` |
| 实际 YAML 配置 | `image`, `memory_limit`, `skills_dir` |

**文档定义**:
```python
@dataclass
class DockerConfig:
    image: str = "tlon-agent:latest"
    work_dir: str = "/workspace"  # ❌ 未在实际配置中使用
    timeout: int = 300  # ❌ 未在实际配置中使用
```

**实际配置**:
```yaml
docker:
  image: "ubuntu:22.04"
  memory_limit: "512m"  # ✅ 实际使用
  skills_dir: "/skills"   # ✅ 实际使用
```

---

#### 问题 5: ConfigModel vs AgentConfig 命名混淆

**config_architecture.md** (第 211-277 行):
```python
@dataclass
class AgentConfig:
    id: str
    name: str
    position_id: str = ""
    # ...
    loading: str = "lazy"  # ❌ 实际代码没有 loading 字段
```

**实际代码** (`src/tlon/base/config/models.py`):
```python
@dataclass
class AgentConfig:
    id: str
    name: str
    position_id: str = ""
    personality: str = ""
    llm: LLMConfig = field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = field(default_factory=DockerRuntimeConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: List[str] = field(default_factory=list)
    respond_to_all: bool = False  # ✅ 实际是 respond_to_all，不是 loading
```

**说明**: `config_architecture.md` 是一个**设计文档**，但其中的模型定义与实际实现不符，且使用了 `loading` 字段而非实际的 `respond_to_all`。

---

### 2.2 命令体系混乱 🔴

#### 问题 6: CLI 命令双重体系

**体系 A: 工程/传统命令** (`docs/user/engineering/commands.md`):
```bash
tlon server      # 启动服务端
tlon tui         # 启动 TUI 客户端
tlon wizard      # 配置向导
```

**体系 B: 文学化命令** (`docs/developer/cli/command-design.md`):
```bash
tlon dream       # 维持梦境（启动 Server）
tlon intrude     # 入侵现实（连接界面）
tlon mirror      # 镜中造物（配置 Agent）
```

**实际代码** (`src/tlon/app/cli/main.py`):
- `tlon server` ✅ 已实现
- `tlon tui` ✅ 已实现
- `tlon wizard` ✅ 已实现
- `tlon dream` ❓ 是否有别名？
- `tlon intrude` ❓ 是否有别名？
- `tlon mirror` ❓ 是否有别名？

**冲突描述**: 
- 工程文档只提到 `server/tui/wizard`
- 文学命令文档提到 `dream/intrude/mirror`
- AGENTS.md 也只提到 `server/tui/wizard`
- 不清楚文学命令是否作为别名实现

**建议**: 在 AGENTS.md 和工程文档中明确说明文学化别名。

---

#### 问题 7: TUI 内命令不一致

**文档 A** (`docs/user/engineering/quickstart.md`):
```
/wake <name>    # 唤醒 Agent
/sleep <name>   # 休眠 Agent
/history        # 查看记忆
/facts          # 查看事实
```

**文档 B** (`docs/user/engineering/commands.md`):
```
/wake <name>     # 唤醒 Agent
/sleep <name>    # 休眠 Agent
/list            # 列出所有 Agent（A 中未提及）
/status <name>   # 查看状态（A 中未提及）
/history [n]     # 显示最近 n 条记忆
/facts           # 显示已知事实
/summary         # 手动触发会话摘要（A 中未提及）
/forget <agent>  # 清除某 Agent 记忆（A 中未提及）
```

**问题**: 
- `quickstart.md` 作为快速入门，命令不完整
- `commands.md` 是完整参考，但两者应该有明确的层次关系

---

#### 问题 8: 配置文件路径混乱

| 文档 | 配置文件路径 |
|------|-------------|
| `config_architecture.md` | `config/agents/`, `config/positions/` |
| `config_design.md` | `config/agents/{id}.yaml`, `config/positions/{id}.yaml` |
| `quickstart.md` | `config/agents.yaml` ❌ |
| `roadmap.md` | `config/agents.yaml` ❌ |

**quickstart.md** 第 106-122 行:
```yaml
# ❌ 错误：使用旧的单文件配置格式
agents:
  - id: "dev-001"
    name: "陈代码"
    position: "developer"
```

**实际代码**:
```yaml
# ✅ 正确：每个 Agent 独立文件 config/agents/{id}.yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"
  position_id: "developer"
```

---

#### 问题 9: 数据目录结构不一致

**quickstart.md** 第 171-192 行:
```
data/                 # 运行时数据 (勿删)
├── agents/
│   └── {id}/
│       ├── memory/
│       │   ├── working.json      # ❌ 实际是 .jsonl
│       │   ├── episodes.json     # ❌ 实际是 .jsonl
│       │   └── facts.json        # ❌ 实际是 .jsonl
│       └── state.json
├── chatroom/
└── logs/
```

**实际代码** (`src/tlon/subsystem/memory/`):
- `working/interactions.jsonl`
- `episodic/summaries.jsonl`
- `episodic/facts.jsonl`

---

### 2.3 架构文档过时 🟡

#### 问题 10: 目录结构文档过时

**config_architecture.md** 第 16-26 行描述的旧结构:
```
minions/
├── agents/
│   ├── config.py          # ❌ 已移动到 base/config/
│   └── agent_registry.py  # ✅ 仍在 agents/
├── positions/
│   ├── models.py          # ✅ 但 Position 现在是 PositionConfig 别名
│   └── registry.py        # ✅
└── wizard/
    ├── agent_store.py     # ✅ 但现在是 ConfigLoader 包装器
    └── position_store.py  # ✅ 但现在是 ConfigLoader 包装器
```

**AGENTS.md** (第 112-211 行) 的目录结构也与实际代码不完全一致。

---

#### 问题 11: 消息流图不一致

**overview.md** (第 213-249 行) 的数据流图:
```
用户输入 → CLI → ServerApp → TlonServer → Event Bus → Congress → Agent
```

**实际代码** (`serverapp.py`):
```
用户输入 → WebSocket → ClientGateway → EventBus → MessageRouter → Agent
                                    ↓
                                 MessageStore
```

**差异**:
- 图中没有 MessageRouter
- 图中没有 MessageStore
- TlonServer 已被重构为 ClientGateway + 多个组件

---

#### 问题 12: 架构层次数不一致

| 文档 | 声称的层数 |
|------|-----------|
| `overview.md` | 7 层 |
| `AGENTS.md` | 7 层 |
| `ARCHITECTURE-UPDATE-SUMMARY.md` | 7 层 |

虽然都声称是 7 层，但各层的具体内容有细微差异：

**AGENTS.md** (第 71-83 行):
```
入口层、应用层、基础层、组织层、Agent 层、Sandbox 层、子系统层
```

**overview.md** (第 12-38 行):
```
入口层、应用层、基础层、组织层、Agent 层、Sandbox 层、子系统层
```

看起来是一致的，但内部组件有差异。

---

### 2.4 路径/配置不一致 🟡

#### 问题 13: Skill 路径格式不一致

| 位置 | 路径格式 |
|------|---------|
| `SkillManager.discover_skills()` | `./skills/{name}` |
| SKILL.md 文档示例 | `/skills/{name}/scripts/xxx.sh` |
| Docker 挂载 | `/skills` |

这会导致 Agent 困惑应该使用哪种格式。

---

#### 问题 14: 导入路径混乱

**旧导入路径（文档中仍有残留）**:
```python
from tlon.protocol import Event, Message  # ❌ 已废弃
from tlon.messaging import EventBus       # ❌ 已废弃
from tlon.agents.docker_runtime import DockerRuntime  # ❌ 已废弃
```

**ARCHITECTURE-UPDATE-SUMMARY.md** 第 124-133 行已标记为废弃，但其他文档可能仍有引用。

---

### 2.5 状态标记混乱 🟢

#### 问题 15: 文档状态标记不一致

| 文档 | 标记系统 |
|------|---------|
| `ARCHITECTURE-UPDATE-SUMMARY.md` | ✅ 已完成, 🔄 待更新 |
| `config-refactor-roadmap.md` | ✅ 已完成 |
| `overview.md` | ✅ 已同步 |
| `deep-dive-architecture-review.md` | 🔴 高, 🟡 中, 🟢 低 |

建议使用统一的标记系统。

---

### 2.6 重复/冗余文档 🟢

#### 问题 16: 多个 roadmap 文件

```
docs/developer/
├── roadmap.md                          # 主要开发计划
├── 02-roadmap/
│   ├── roadmap.md                      # 另一个 roadmap
│   ├── agent_position_roadmap.md
│   └── refactor_event_plan.md
├── config-refactor-roadmap.md          # Config 重构
└── roadmap-config-refactor.md          # 另一个 Config 重构
```

**问题**:
- `roadmap.md` vs `02-roadmap/roadmap.md` - 内容高度重复
- `config-refactor-roadmap.md` vs `roadmap-config-refactor.md` - 命名混乱

---

#### 问题 17: 多个架构文档

```
docs/developer/01-architecture/
├── overview.md              # 架构总览
├── memory.md                # Memory 系统
├── messaging-refactor-v2.md # 消息重构
├── multi-agent.md           # 多 Agent
└── ui-architecture.md       # UI 架构

docs/developer/
├── architecture-with-sandbox.md    # 另一个架构文档
├── agent-loading-flow.md           # Agent 加载流程
└── ARCHITECTURE-UPDATE-SUMMARY.md  # 架构更新总结
```

**问题**: 
- `overview.md` 与 `architecture-with-sandbox.md` 内容重叠
- `ARCHITECTURE-UPDATE-SUMMARY.md` 应该是更新日志，但也包含架构描述

---

#### 问题 18: Config 文档重复

```
docs/developer/config/
├── config_architecture.md      # 配置架构
├── config_design.md            # 配置设计
└── config_roadmap.md           # 配置路线图

docs/developer/
├── config-refactor-roadmap.md      # Config 重构路线图
└── config-refactor-summary.md      # Config 重构总结
```

**config_architecture.md** vs **config_design.md**:
- 两者都是关于配置系统的设计
- `config_architecture.md` 更详细，包含代码示例
- `config_design.md` 更概念化

---

### 2.7 缺失文档 🟡

#### 问题 19: AGENTS.md 标记为待更新

**ARCHITECTURE-UPDATE-SUMMARY.md** 第 168 行:
```
| `AGENTS.md` | 项目结构、导入路径、开发指南 | 🔄 待更新 |
```

实际检查 AGENTS.md:
- 第 178-211 行的导入路径是正确的
- 但可能还有其他内容需要更新

---

#### 问题 20: 用户文档目录结构混乱

**docs/README.md** 描述的目录结构:
```
user/
├── 01-product/          # ❌ 实际不存在
├── 02-interface/        # ❌ 实际不存在
└── 03-configuration/    # ❌ 实际不存在
```

**实际结构**:
```
user/
├── 01-getting-started.md
├── 02-first-conversation.md
├── 03-the-congress.md
├── 04-memory.md
├── 05-library.md
├── 06-mirror.md
├── 07-garden.md
├── 08-ruins.md
├── README.md
└── engineering/
    ├── README.md
    ├── architecture.md
    ├── commands.md
    ├── configuration.md
    ├── quickstart.md
    └── troubleshooting.md
```

---

## 3. 根因分析

### 3.1 项目演进过快

- 代码重构频繁（config 模块、架构分层、消息系统）
- 文档更新滞后于代码变更
- 多个迭代版本的设计文档并存

### 3.2 缺乏文档治理

- 没有统一的文档模板
- 没有文档更新流程
- 没有定期的文档审查机制

### 3.3 命名决策不明确

- Tlon vs Minions 的命名问题长期未解决
- 术语变更后没有全量更新（如 position → position_id）

### 3.4 多人协作问题

- 不同文档作者使用不同的标记习惯
- 缺乏文档风格指南

---

## 4. 建议修复计划

### 阶段 1: 紧急修复（本周）

#### 修复 1: 统一项目名称
- [ ] 将 `docs/` 中所有 "Minions" 替换为 "Tlon"
- [ ] 更新 `docs/README.md` 的描述
- [ ] 统一 Python 包名保持 `tlon`（小写）

#### 修复 2: 修正配置字段
- [ ] 更新 `quickstart.md` 第 106-122 行：`position` → `position_id`
- [ ] 更新 `quickstart.md` 中的 `agents.yaml` 示例格式
- [ ] 更新 `config_architecture.md` 中的 `LLMConfig` 字段名

#### 修复 3: 统一命令文档
- [ ] 在 `AGENTS.md` 中明确说明文学化命令别名
- [ ] 在 `command-design.md` 中说明向后兼容的传统命令

### 阶段 2: 清理冗余（下周）

#### 修复 4: 合并 roadmap 文件
- [ ] 合并 `roadmap.md` 和 `02-roadmap/roadmap.md`
- [ ] 合并 `config-refactor-roadmap.md` 和 `roadmap-config-refactor.md`
- [ ] 删除过时的 roadmap 文件

#### 修复 5: 合并 config 文档
- [ ] 评估 `config_architecture.md` 和 `config_design.md`
- [ ] 保留一个作为主文档，另一个归档或删除

### 阶段 3: 架构同步（后续）

#### 修复 6: 更新架构图
- [ ] 更新 `overview.md` 中的数据流图（添加 MessageRouter）
- [ ] 更新所有目录结构示意图
- [ ] 确保 AGENTS.md 与实际代码一致

#### 修复 7: 建立文档规范
- [ ] 创建 `docs/CONTRIBUTING.md` 文档编写规范
- [ ] 统一状态标记系统
- [ ] 建立文档审查清单

---

## 5. 优先级矩阵

| 优先级 | 问题 | 影响 |
|--------|------|------|
| 🔴 P0 | 项目名称混乱 | 品牌认知混乱 |
| 🔴 P0 | 配置字段不一致 | 用户配置失败 |
| 🔴 P0 | 配置文件路径错误 | 系统无法启动 |
| 🟡 P1 | 命令体系混乱 | 用户困惑 |
| 🟡 P1 | 数据目录结构错误 | 误导开发者 |
| 🟡 P1 | 文档目录结构描述错误 | 导航困难 |
| 🟢 P2 | 冗余文档 | 维护负担 |
| 🟢 P2 | 状态标记不一致 | 美观问题 |

---

## 6. 附录

### 6.1 文档清单

| 文档路径 | 类型 | 状态 | 备注 |
|----------|------|------|------|
| `README.md` | 入口 | ✅ 较新 | 使用 Tlon |
| `AGENTS.md` | 开发指南 | 🔄 部分过时 | 需检查导入路径 |
| `docs/README.md` | 文档索引 | ❌ 过时 | 使用 Minions |
| `docs/developer/overview.md` | 架构 | 🔄 部分过时 | MessageRouter 未体现 |
| `docs/user/engineering/quickstart.md` | 用户指南 | ❌ 过时 | 配置格式错误 |

### 6.2 代码验证脚本

```bash
# 检查项目名称使用情况
grep -r "Minions" docs/ --include="*.md" | wc -l
grep -r "Tlon" docs/ --include="*.md" | wc -l

# 检查配置字段
grep -r "position_id" docs/ --include="*.md" | wc -l
grep -r "position:" docs/ --include="*.md" | wc -l

# 检查命令文档
grep -r "dream\|intrude\|mirror" docs/user --include="*.md"
```

---

*报告生成时间: 2026-03-16*  
*审查者: 架构师模式 (Architect Mode)*
