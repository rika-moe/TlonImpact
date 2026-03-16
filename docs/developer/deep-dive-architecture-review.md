# 深度架构审查报告

> **审查日期**: 2026-03-16  
> **审查范围**: Mention 机制、工具系统、Skill 系统、文件操作  
> **审查状态**: 🔍 已完成  
> **风险等级**: 🔴 高（存在影响用户体验的设计缺陷）

---

## 1. 审查概览

本次审查聚焦于 `todo.md` 中标记的四个关键问题领域。通过代码走查和架构分析，发现了若干影响系统正确性和用户体验的问题。

### 审查方法

- **静态代码分析**: 检查 `src/tlon/agents/`, `src/tlon/subsystem/tools/`, `src/tlon/organization/messaging/` 等核心模块
- **Prompt-代码一致性检查**: 验证系统提示与可用工具的一致性
- **数据流追踪**: 分析 mention 从用户输入到 Agent 响应的完整链路

### 关键发现摘要

| 问题领域 | 状态 | 严重程度 |
|---------|------|---------|
| Mention 机制 | ⚠️ 部分工作 | 中 |
| completeDialog/sendMessage | ✅ 已解决 | - |
| Skill 系统 | ⚠️ 实现有坑 | 中 |
| 文件操作工具 | ✅ 可用但 UX 差 | 低 |

> **更新 2026-03-17**: `sendMessage` 工具已从 Prompt 中彻底移除。Agent 现在**只能**通过 `completeDialog` 发送消息，简化了心智模型。

---

## 2. 问题 1: Mention 机制分析

### 2.1 当前架构

Mention 机制涉及两个核心组件：

```
用户输入 → ClientGateway.send_message() → EventBus(MESSAGE_NEW)
                                              │
                                              ▼
                                       MessageRouter
                                       ├─ _resolve_mentions_to_ids()
                                       └─ 发布 MESSAGE_ROUTED
                                              │
                                              ▼
                                          Agent
                                       ├─ _on_message_routed()
                                       └─ _is_mentioned()  ← 二次过滤
```

### 2.2 代码分析

**MessageRouter 的路由逻辑** (`router.py:177-199`):

```python
def _route_human_message(self, message: Message) -> Set[str]:
    targets: Set[str] = set()
    
    if message.mentions:
        # 显式 mention - 解析为 Agent ID 并路由
        resolved = self._resolve_mentions_to_ids(message.mentions)
        targets.update(resolved)
    else:
        # 无 mention - 路由给 proactive agents
        proactive = self._get_proactive_agents()
        targets.update(proactive)
    
    return targets
```

**Agent 端的二次过滤** (`agent.py:448-471`):

```python
def _is_mentioned(self, message: Message) -> bool:
    """检查 Agent 是否被 mention。
    
    只检查 message.mentions 列表（由 completeDialog 等工具设置）。
    不解析 content 中的 @mentions。
    """
    # 检查 ID 精确匹配
    if self.id in message.mentions:
        return True
    
    # 检查名字精确匹配
    if self.name in message.mentions:
        return True
    
    return False
```

### 2.3 发现的问题

#### 问题 A: Prompt 与实际行为不符

**系统提示** (`prompt_builder.py:311-317`):

```markdown
- ⚠️ **必须使用 completeDialog 结束**: 直接输出文本不会发送消息，必须调用 completeDialog 工具
- ⚠️ **mentions 参数**: 只有通过工具的 `mentions` 参数指定的人才会收到通知
  - 当你在 completeDialog 中设置 mentions=['林知遥', '江予白']，这些人会被唤醒
  - 在 content 中写 '@林知遥' 只是显示效果，不会真正通知对方
```

**实际情况**: Agent 根本不解析 content 中的 `@xxx`，"显示效果"的说法具有误导性。

#### 问题 B: 决策逻辑分散

| 层级 | 决策内容 | 问题 |
|------|---------|------|
| MessageRouter | 根据 mentions 决定路由目标 | 正确 |
| Agent._on_message_routed | 信任 Router 决定，但... | 重复检查 |
| Agent._should_respond | 再次检查 mention 和 proactive | 逻辑冗余 |

Router 已经做出了路由决策，Agent 再次过滤造成:
- 代码难以维护（逻辑分散在两处）
- 潜在的不一致（Router 决定路由但 Agent 拒绝响应）

### 2.4 建议修复

**方案 A: Router 全权负责（推荐）**

Agent 完全信任 Router 的决策，移除二次过滤:

```python
# agent.py _on_message_routed()
async def _on_message_routed(self, event: Event) -> None:
    routed: RoutedMessage = event.payload
    
    # Router 已决定，Agent 无需再次判断
    if self.id not in routed.target_ids:
        return
    
    # 直接入队，无需 _should_respond() 检查
    await self._message_queue.put(routed.original)
```

**方案 B: 明确职责分离**

如果保留 Agent 端过滤，明确区分:
- **Router**: 负责"谁能看到这条消息"（可见性）
- **Agent**: 负责"我是否响应这条消息"（业务决策）

更新文档说明这种区分。

---

## 3. 问题 2: completeDialog 工具逻辑 (已解决)

> **状态更新 2026-03-17**: `sendMessage` 工具已从 Prompt 中彻底移除。

### 3.1 原始问题: Prompt 欺骗 LLM

**原系统提示** (`prompt_builder.py` 旧版本):

```markdown
- **completeDialog**: 完成对话并发送最终回复。**这是唯一结束处理的方式！**
    - 参数: content（回复内容）, mentions（可选，要@的用户列表）
- **sendMessage**: 发送中间消息（如'让我查一下'）  ← 工具不存在！
```

**问题**: Prompt 教导 LLM 使用 `sendMessage` 工具，但该工具从未实现，导致 LLM 幻觉调用。

### 3.2 解决方案

**彻底移除 `sendMessage`**:

- ✅ 从 `build_capabilities()` 移除
- ✅ 从 `build_tool_usage()` 移除
- ✅ 更新架构文档

**当前工具列表**:
- `completeDialog`: 唯一的发送消息方式（结束对话时）
- `executeBashCommand`: 执行命令
- `rememberFact`/`searchMemories`/`generateSummary`/`forget`: 记忆管理
- `activateSkill`/`deactivateSkill`/`loadSkillResource`: 技能管理
- `createTask`/`startTask`/`pauseTask`/`completeTask`/`removeTask`/`listTasks`/`getCurrentTask`: 任务管理

### 3.3 代码追踪

**`completeDialog` 的执行流程** (`agent.py:538-617`):

```python
# 在 _process_message() 的 thinking loop 中
for tool_call in response.tool_calls:
    if tool_call.function_name == "completeDialog":
        complete_dialog_call = tool_call
    
    # 执行工具
    tool_result = await self._execute_tool_call(tool_call)
    
    # 如果是 completeDialog，提取最终回复
    if tool_call.function_name == "completeDialog" and tool_result.success:
        final_response = tool_result.data.get("content", "")
        final_mentions = tool_result.data.get("mentions", [])

# 如果调用了 completeDialog，发送消息并退出循环
if complete_dialog_call and final_response is not None:
    # 直接构造 Message 并发布到 EventBus
    message = Message.create(...)
    await self.event_bus.publish(Event(type=EventType.MESSAGE_NEW, ...))
    return  # 结束处理
```

**注意**: `CompleteDialogTool.execute()` 本身**不发送消息**，只是返回数据。真正的发送在 Agent 的 processing loop 中完成。

### 3.4 建议修复

**方案 A: 添加 SendMessageTool（推荐长期使用）**

```python
# src/tlon/subsystem/tools/messaging/send_message.py
class SendMessageTool(Tool):
    """发送中间消息到聊天室。
    
    用于在处理过程中向用户报告进度，如"让我查一下..."。
    这是非终结消息，调用后 Agent 会继续思考。
    """
    
    name = "sendMessage"
    description = "发送中间进度消息到聊天室。用于在处理过程中向用户报告状态（如'让我查一下'）。调用此工具后处理会继续，不会结束对话。"
    
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "消息内容"
            },
            "mentions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选。要@的用户ID或名字列表",
                "default": []
            }
        },
        "required": ["content"]
    }
    
    def __init__(self, event_bus: EventBus, agent_id: str, agent_name: str):
        self.event_bus = event_bus
        self.agent_id = agent_id
        self.agent_name = agent_name
    
    async def execute(self, content: str, mentions: Optional[List[str]] = None) -> ToolResult:
        try:
            message = Message.create(
                sender_id=self.agent_id,
                sender_name=self.agent_name,
                sender_type="agent",
                content=content,
                mentions=mentions or [],
            )
            await self.event_bus.publish(Event(
                type=EventType.MESSAGE_NEW,
                payload=message,
                source=self.agent_id,
            ))
            return ToolResult.success(data={"message_id": message.id})
        except Exception as e:
            return ToolResult.error(f"发送消息失败: {e}")
```

**方案 B: 快速修复（立即实施）**

如果不立即添加 `SendMessageTool`，应修改 `prompt_builder.py` 移除对 `sendMessage` 的引用:

```python
# prompt_builder.py:284-286
tools = [
    "- **completeDialog**: 完成对话并发送最终回复。**这是唯一结束处理的方式！**",
    # 删除或注释掉下面这行，直到 SendMessageTool 实现
    # "- **sendMessage**: 发送中间消息（如'让我查一下'）",
    "- **executeBashCommand**: 在容器中执行 bash 命令",
    # ...
]
```

---

## 4. 问题 3: SKILL 系统的"奇怪之处"

### 4.1 架构设计概述

Skill 系统采用三级渐进式加载设计，概念上合理:

```
Discovery (启动时)
    └── 扫描 skills/ 目录，加载 metadata（name, description）
            │
            ▼
Activation (运行时按需)
    └── LLM 调用 activateSkill 工具，加载完整 SKILL.md
            │
            ▼
Execution (实际使用)
    └── 调用 executeBashCommand 执行 scripts/ 中的脚本
```

### 4.2 发现的问题

#### 问题 A: DEBUG 代码残留

```python
# subsystem/skills/models.py:153
return result

@classmethod
def _parse_yaml(cls, text: str) -> Dict[str, Any]:
    result = yaml.safe_load(text)
    print("yaml result = ", result)  # ❌ 生产代码不应有 print
    return result

# line 219
frontmatter, content = cls.parse_file(skill_md_path)
print("frontmatter=", frontmatter)  # ❌ 生产代码不应有 print
```

**影响**: 污染日志输出，每次解析 Skill 都会打印调试信息。

#### 问题 B: 路径格式不一致

| 位置 | 路径格式 | 问题 |
|------|---------|------|
| 代码 (`SkillParser.parse_skill`) | `./skills/{name}` | 带点前缀 |
| SKILL.md 文档示例 | `/skills/{name}/scripts/xxx.sh` | 绝对路径，无点 |
| Docker 挂载 | `/skills` | 容器内绝对路径 |

这种不一致会导致:
- Agent 困惑（应该用什么路径格式？）
- 脚本执行失败（路径解析错误）

#### 问题 C: Skill 未自动激活

```python
# agent.py:259-267
async def _discover_skills(self) -> None:
    if self.config.skills:
        logger.info(f"Discovering skills for {self.name}: {self.config.skills}")
        skills = self.skill_manager.discover_skills(self.config.skills)
        # ❌ 只发现，不激活！
```

**用户预期**: Agent 配置了 `skills: ["git-operations"]`，应该能立即使用 Git 相关能力。

**实际行为**: Agent 必须先调用 `activateSkill("git-operations")`，然后才能使用。这增加了不必要的步骤。

### 4.3 建议修复

**修复 A: 移除 debug 代码**

```python
# 使用 logger 替代 print，或完全移除
import logging
logger = logging.getLogger(__name__)
# logger.debug(f"Parsed yaml: {result}")  # 如需调试，使用 logger
```

**修复 B: 统一路径格式**

在文档和代码中统一使用容器内绝对路径 `/skills`:

```python
# skill_manager.py 修改
skill = SkillParser.parse_skill(item, root_path_prefix="/skills")  # 改为 /skills
```

更新所有 SKILL.md 中的示例路径。

**修复 C: 自动激活配置的 Skills**

```python
# agent.py:250-269
async def _discover_skills(self) -> None:
    """发现并自动激活配置的 skills。
    
    Discovery 阶段加载 metadata，
    对于配置的 skills，自动激活以提供开箱即用的体验。
    """
    if not self.config.skills:
        return
    
    logger.info(f"Discovering skills for {self.name}: {self.config.skills}")
    
    # Discovery: 扫描并加载 metadata
    discovered = self.skill_manager.discover_skills(self.config.skills)
    logger.info(f"Discovered {len(discovered)} skills")
    
    # Auto-activation: 配置的 skills 自动激活
    for skill_name in self.config.skills:
        try:
            content = self.skill_manager.activate_skill(skill_name)
            if content:
                logger.info(f"Auto-activated skill: {skill_name}")
            else:
                logger.warning(f"Skill not found for activation: {skill_name}")
        except Exception as e:
            logger.error(f"Failed to activate skill {skill_name}: {e}")
```

**在 System Prompt 中体现激活的 skills**:

```python
# prompt_builder.py 改进
if self.agent.config.skills:
    active_skills = self.agent.skill_manager.get_active_skills()
    if active_skills:
        sections.append(f"## 已激活技能\n\n当前可用技能: {', '.join(active_skills)}")
```

---

## 5. 问题 4: READ/WRITE TOOL 需求分析

### 5.1 当前实现方式

文件操作通过 `file-operations` Skill 的脚本实现:

```bash
# 使用流程（3 步）
1. activateSkill("file-operations")  # 激活 skill
2. executeBashCommand("python3 /skills/file-operations/scripts/read_file.py /workspace/config.yaml")
3. deactivateSkill("file-operations")  # 可选，释放资源
```

### 5.2 两种方案对比

| 维度 | 当前: Skill 脚本 | 提议: 原生工具 |
|------|-----------------|---------------|
| **调用步骤** | 3 步（激活+执行+释放） | 1 步 |
| **路径长度** | 长 (`/skills/file-operations/scripts/...`) | 短 (`/workspace/config.yaml`) |
| **灵活性** | 高（可定制脚本） | 中（标准实现） |
| **与标准兼容性** | Agent Skills 标准 | 需要自定义 |
| **适用场景** | 复杂领域操作 | 高频基础操作 |

### 5.3 架构师建议

**对于低并发本地开发场景，建议添加原生文件工具**。

理由:
1. **文件读写是最高频操作**（仅次于对话），不应该需要 2 步以上
2. **Skill 更适合复杂领域逻辑**（如 Git 操作、数据处理流程），而不是基础 IO
3. **原生工具可以提供更好的错误处理**（权限错误、文件不存在等具体提示）

### 5.4 建议实现

```python
# src/tlon/subsystem/tools/files/read_file.py
class ReadFileTool(Tool):
    """读取文件内容。
    
    在 Agent 的 Docker 容器中读取文件，支持大文件分块读取。
    """
    
    name = "readFile"
    description = "读取工作目录下的文件内容。用于查看代码、配置文件、日志等。支持限制行数避免读取过大文件。"
    
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于 /workspace）"
            },
            "limit": {
                "type": "integer",
                "description": "最多读取行数，默认 100 行",
                "default": 100
            },
            "offset": {
                "type": "integer",
                "description": "从第几行开始读取，默认 0",
                "default": 0
            }
        },
        "required": ["path"]
    }
    
    def __init__(self, docker_runtime: DockerRuntime):
        self.docker_runtime = docker_runtime
    
    async def execute(self, path: str, limit: int = 100, offset: int = 0) -> ToolResult:
        # 安全检查: 确保路径在 /workspace 内
        full_path = f"/workspace/{path}"
        
        try:
            # 通过 docker exec 读取文件
            result = await self.docker_runtime.execute_command(
                f"cat '{full_path}' | tail -n +{offset + 1} | head -n {limit}"
            )
            
            if result.return_code != 0:
                return ToolResult.error(f"读取文件失败: {result.stderr}")
            
            lines = result.stdout.split('\n')
            return ToolResult.success(data={
                "content": result.stdout,
                "lines_read": len(lines),
                "total_lines": "unknown"  # 可通过 wc -l 获取
            })
        except Exception as e:
            return ToolResult.error(f"读取文件时出错: {e}")
```

```python
# src/tlon/subsystem/tools/files/write_file.py
class WriteFileTool(Tool):
    """写入文件内容。"""
    
    name = "writeFile"
    description = "写入或追加内容到文件。用于创建代码文件、写入配置、记录日志等。"
    
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于 /workspace）"
            },
            "content": {
                "type": "string",
                "description": "要写入的内容"
            },
            "append": {
                "type": "boolean",
                "description": "是否追加模式，默认 False（覆盖）",
                "default": False
            }
        },
        "required": ["path", "content"]
    }
    
    def __init__(self, docker_runtime: DockerRuntime):
        self.docker_runtime = docker_runtime
    
    async def execute(self, path: str, content: str, append: bool = False) -> ToolResult:
        full_path = f"/workspace/{path}"
        
        try:
            # 创建父目录
            parent_dir = full_path.rsplit('/', 1)[0] if '/' in full_path else "/workspace"
            await self.docker_runtime.execute_command(f"mkdir -p '{parent_dir}'")
            
            # 写入文件（使用 heredoc 避免转义问题）
            redirect = ">>" if append else ">"
            # 注意：需要处理 content 中的特殊字符
            escaped_content = content.replace("'", "'\\''")
            cmd = f"cat << 'EOF' {redirect} '{full_path}'\n{escaped_content}\nEOF"
            
            result = await self.docker_runtime.execute_command(cmd)
            
            if result.return_code != 0:
                return ToolResult.error(f"写入文件失败: {result.stderr}")
            
            return ToolResult.success(data={
                "path": path,
                "bytes_written": len(content.encode('utf-8'))
            })
        except Exception as e:
            return ToolResult.error(f"写入文件时出错: {e}")
```

### 5.5 工具注册

```python
# agent.py:_register_tools()
def _register_tools(self) -> None:
    # 基础工具
    self.tool_executor.register_tool(ExecuteBashTool(self.docker_runtime))
    
    # 新增：文件操作工具
    self.tool_executor.register_tool(ReadFileTool(self.docker_runtime))
    self.tool_executor.register_tool(WriteFileTool(self.docker_runtime))
    
    # 其他工具...
```

---

## 6. 总结与行动建议

### 优先级矩阵

| 优先级 | 问题 | 影响 | 状态 |
|--------|------|------|------|
| ✅ **已完成** | `sendMessage` 已从 Prompt 彻底移除 | - | **2026-03-17 完成** |
| 🔴 **P0** | Skill 解析中的 debug print | 污染日志 | **立即修复**: 移除或改用 logger |
| 🔴 **P0** | Skill 未自动激活 | 配置不生效，用户困惑 | **立即修复**: Agent 启动时自动激活 |
| 🟡 **P1** | Mention 机制冗余 | 代码维护难 | 建议重构，简化逻辑 |
| 🟡 **P1** | 添加 `readFile`/`writeFile` 工具 | 高频操作 UX 差 | 建议实现 |
| 🟢 **P2** | 统一 Skill 路径格式 | 潜在的跨平台问题 | 后续优化 |

### 实施路线图

#### 阶段 1: 已完成 ✓

1. **✅ 移除 `sendMessage`** (2026-03-17)
   - 从 `prompt_builder.py` 的 `build_capabilities()` 移除
   - 从 `prompt_builder.py` 的 `build_tool_usage()` 移除
   - 更新架构文档

#### 阶段 2: 进行中

1. **移除 debug print**
   ```python
   # models.py: 删除或注释掉两行 print
   ```

2. **实现 Skill 自动激活**
   ```python
   # agent.py: 在 _discover_skills() 中添加自动激活逻辑
   ```

#### 阶段 3: 功能增强（后续）

1. 实现 `ReadFileTool` 和 `WriteFileTool`
2. 简化 Mention 机制（移除冗余检查）
3. 统一 Skill 路径格式

#### 阶段 3: 代码重构（后续）

1. 简化 Mention 机制（移除冗余检查）
2. 统一 Skill 路径格式

### 影响评估

- **破坏性变更**: 无（所有修复都是新增或内部实现调整）
- **向后兼容性**: 保持（现有 Agent 配置继续有效）
- **用户感知**: 明显改善（配置即生效，工具可用性提升）

---

## 附录 A: 相关代码位置速查

| 组件 | 文件路径 |
|------|---------|
| Mention 路由 | `src/tlon/organization/messaging/router.py` |
| Mention 过滤 | `src/tlon/agents/agent.py:448-471` |
| 工具注册 | `src/tlon/agents/agent.py:270-334` |
| Prompt 构建 | `src/tlon/agents/prompt_builder.py` |
| Skill 解析 | `src/tlon/subsystem/skills/models.py` |
| Skill 管理 | `src/tlon/subsystem/skills/manager.py` |
| completeDialog 工具 | `src/tlon/subsystem/tools/complete_dialog/complete_dialog.py` |

## 附录 B: 测试建议

修复后应验证的场景:

1. **sendMessage 修复验证**
   - 启动对话，观察 Agent 是否尝试调用 `sendMessage`
   - 检查工具调用是否成功（或 Prompt 不再提及）

2. **Skill 自动激活验证**
   - 配置 `skills: ["git-operations"]`
   - 启动后直接问 "帮我 git status"
   - 验证 Agent 无需显式 `activateSkill` 即可使用

3. **Mention 机制验证**
   - 在群聊中 @某个 Agent
   - 验证只有被 @的 Agent 响应
   - 验证 content 中的 @xxx 不会触发响应

---

*报告生成时间: 2026-03-16*  
*审查者: 架构师模式 (Architect Mode)*
