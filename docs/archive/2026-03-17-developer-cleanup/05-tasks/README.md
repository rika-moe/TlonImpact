# Agent Personal Task Manager 设计文档

> 版本: 1.1  
> 更新日期: 2026-03-08  
> 状态: 设计完成，待实现

---

## 1. 概述

### 1.1 设计目标

为单个 Agent 提供轻量级的**个人任务管理**能力，使其能够将复杂的大任务拆解成可管理的小任务，并逐个跟踪执行。支持任务中途暂停和恢复，方便处理临时插入的紧急任务。

### 1.2 核心特性

- **单层扁平结构**：没有父子层级，所有任务平等
- **四状态管理**：`todo` | `doing` | `paused` | `done`
- **暂停恢复机制**：支持任务中途暂停，稍后恢复继续
- **持久化存储**：重启后自动恢复任务状态
- **自清理机制**：支持移除已完成的任务，防止列表膨胀
- **单线程执行**：一次只执行一个任务，切换时自动暂停前一个

### 1.3 使用场景

```
场景1: 标准拆解执行
用户: @开发-A 帮我实现一个用户登录系统
开发-A: 拆解成多个小任务 → 逐个执行 → 全部完成

场景2: 中途插入紧急任务
用户: @开发-A 先停一下，帮我修个紧急bug
开发-A: 暂停当前任务 → 处理bug → 恢复原任务

场景3: 多任务交替
开发-A: 任务A做到一半 → 切换到任务B → 再切回任务A继续
```

---

## 2. 数据模型

### 2.1 Task 结构

```python
@dataclass
class Task:
    id: str              # 短ID，如 "t-001"
    title: str           # 任务标题
    description: str     # 任务描述（可选）
    status: str          # "todo" | "doing" | "paused" | "done"
    
    created_at: str      # ISO格式创建时间
    started_at: str      # 开始时间
    paused_at: str       # 暂停时间
    completed_at: str    # 完成时间
    
    result: str          # 完成结果/产出摘要
    pause_count: int     # 被暂停次数（用于统计）
```

### 2.2 状态流转

```
                     startTask               completeTask
[todo] ─────────────────────────→ [doing] ────────────────────→ [done]
                                    ↑   │
                     pauseTask      │   │  pauseTask
                        ┌───────────┘   └───────────┐
                        ↓                           ↓
                     [paused] ←───────────────── [paused]
                        │                           ↑
                        └───────────────────────────┘
                              startTask (恢复)
```

### 2.3 状态说明

| 状态 | 含义 | 触发条件 |
|------|------|----------|
| `todo` | 待处理 | 任务创建后的初始状态 |
| `doing` | 进行中 | startTask 调用后，当前活跃任务 |
| `paused` | 已暂停 | pauseTask 调用，或 startTask 其他任务时自动暂停 |
| `done` | 已完成 | completeTask 调用后 |

---

## 3. 工具集定义

### 3.1 createTask - 创建任务

**描述**：创建一个新任务，加入任务列表

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 任务标题，简短描述任务内容 |
| `description` | string | ❌ | 任务详细描述 |

**返回值**：
```json
{
  "success": true,
  "task_id": "t-001",
  "title": "设计登录API接口",
  "status": "todo",
  "message": "任务已创建"
}
```

**使用场景**：
- 接收到复杂任务时，拆解成多个小任务
- 执行过程中发现需要额外步骤时，添加新任务

---

### 3.2 listTasks - 查看任务列表

**描述**：查看当前所有任务及其状态

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `filter` | string | ❌ | 过滤条件: "all"(默认) \| "todo" \| "doing" \| "paused" \| "done" |

**返回值**：
```json
{
  "success": true,
  "total": 5,
  "doing_count": 1,
  "paused_count": 1,
  "todo_count": 2,
  "done_count": 1,
  "tasks": [
    {"id": "t-001", "title": "设计登录API接口", "status": "done"},
    {"id": "t-002", "title": "实现后端JWT验证", "status": "paused", "paused_at": "..."},
    {"id": "t-003", "title": "实现前端登录页面", "status": "todo"},
    {"id": "t-004", "title": "联调测试", "status": "todo"},
    {"id": "t-005", "title": "修复登录bug", "status": "doing"}
  ]
}
```

**显示格式**：
```
📋 任务列表 (共 5 个)
   进行中: 1 | 暂停: 1 | 待处理: 2 | 已完成: 1

[1] ✅ done   | 设计登录API接口 (t-001)
[2] ⏸️ paused | 实现后端JWT验证 (t-002) [暂停5分钟]
[3] ⏳ todo   | 实现前端登录页面 (t-003)
[4] ⏳ todo   | 联调测试 (t-004)
[5] 🔄 doing  | 修复登录bug (t-005) ← 当前
```

---

### 3.3 startTask - 开始/恢复任务

**描述**：开始执行指定任务。如果当前有其他进行中的任务，会自动将其暂停。

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `task_id` | string | ✅ | 要开始或恢复的任务ID |

**行为**：
1. 如果当前有 `doing` 状态的任务 → **自动暂停**该任务
2. 将目标任务状态设为 `doing`
3. 如果是从 `paused` 恢复，记录恢复时间

**返回值**：
```json
{
  "success": true,
  "task_id": "t-005",
  "title": "修复登录bug",
  "status": "doing",
  "auto_paused": {
    "task_id": "t-002",
    "title": "实现后端JWT验证",
    "duration_before_pause": "15分钟"
  },
  "message": "已开始执行任务，已自动暂停之前的任务"
}
```

**使用场景**：
- 开始执行新任务
- 从暂停状态恢复继续之前的任务
- 切换到其他任务（自动暂停当前）

---

### 3.4 pauseTask - 暂停任务

**描述**：主动暂停当前正在进行的任务

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `task_id` | string | ✅ | 要暂停的任务ID |
| `reason` | string | ❌ | 暂停原因（可选）|

**约束**：
- 只能暂停 `doing` 状态的任务
- 暂停时自动记录 `paused_at` 时间
- `pause_count` 计数器 +1

**返回值**：
```json
{
  "success": true,
  "task_id": "t-002",
  "title": "实现后端JWT验证",
  "status": "paused",
  "paused_at": "2026-03-08T10:30:00",
  "progress": "完成了JWT生成，待做验证部分",
  "message": "任务已暂停，可随时恢复继续"
}
```

**使用场景**：
- 用户要求暂停当前任务处理其他事
- 临时插入紧急任务，需要暂停当前工作
- 主动暂停记录当前进度，稍后继续

---

### 3.5 completeTask - 完成任务

**描述**：标记任务为已完成，并记录执行结果

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `task_id` | string | ✅ | 要完成的任务ID |
| `result` | string | ❌ | 执行结果摘要，记录产出内容 |

**行为**：
1. 将任务状态设为 `done`
2. 记录 `completed_at` 时间
3. 计算并记录实际耗时
4. 保存执行结果

**约束**：
- 可以从 `doing` 或 `paused` 状态完成
- `todo` 状态的任务需要先 start 才能 complete

**返回值**：
```json
{
  "success": true,
  "task_id": "t-002",
  "title": "实现后端JWT验证",
  "status": "done",
  "duration_minutes": 25,
  "pause_count": 1,
  "message": "任务已完成"
}
```

---

### 3.6 removeTask - 移除任务

**描述**：从任务列表中永久移除指定任务

**参数**：

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `task_id` | string | ✅ | 要移除的任务ID |

**约束**：
- 只能移除 `done` 状态的任务
- `todo`/`doing`/`paused` 的任务需先处理完成才能移除
- 移除后不可恢复

**返回值**：
```json
{
  "success": true,
  "task_id": "t-001",
  "title": "设计登录API接口",
  "message": "任务已移除"
}
```

**使用场景**：
- 定期清理已完成的任务，保持列表整洁
- 建议每完成 3-5 个任务后清理一次

---

### 3.7 getCurrentTask - 获取当前任务

**描述**：获取当前正在执行的任务信息

**参数**：无

**返回值**：
```json
{
  "success": true,
  "has_task": true,
  "task": {
    "id": "t-005",
    "title": "修复登录bug",
    "description": "紧急修复token过期判断问题",
    "status": "doing",
    "started_at": "2026-03-08T10:30:00",
    "elapsed_minutes": 5
  }
}
```

**无任务时返回**：
```json
{
  "success": true,
  "has_task": false,
  "message": "当前没有进行中的任务"
}
```

**说明**：
- 优先返回 `doing` 状态的任务
- 如果没有 `doing` 的任务，返回最近 `paused` 的任务
- 用于 Agent 确认当前工作状态

---

## 4. 工作流程

### 4.1 标准工作流

```
接收复杂任务
    ↓
┌─────────────────────────────────────┐
│ 1. 拆解任务                          │
│    createTask(title="步骤1")        │
│    createTask(title="步骤2")        │
│    ...                              │
└─────────────────────────────────────┘
    ↓
开始循环执行:
    ↓
┌─────────────────────────────────────┐
│ 2. 开始任务                          │
│    startTask(task_id="t-xxx")       │
│    (自动暂停之前的任务)               │
└─────────────────────────────────────┘
    ↓
执行任务内容（使用其他工具）
    ↓
遇到中断/切换?
    ├── 是 → pauseTask(当前) → startTask(新任务)
    ↓
任务完成?
    ├── 是 → completeTask(task_id, result)
    ↓
┌─────────────────────────────────────┐
│ 3. 清理任务（可选）                   │
│    removeTask(task_id)              │
└─────────────────────────────────────┘
    ↓
还有未完成任务? → 继续循环
    ↓
所有任务完成!
```

### 4.2 场景示例

#### 场景1: 中途插入紧急任务

```
[人类] @开发-A 先停一下，帮我修个紧急bug

[开发-A] 好的，我暂停当前任务处理bug

→ getCurrentTask()
  返回: 实现后端JWT验证 (doing)

→ pauseTask(task_id="t-002", reason="用户要求处理紧急bug")
  ✅ 已暂停: 实现后端JWT验证
     当前进度: 完成了JWT生成，待做验证部分

→ createTask(title="修复登录失效bug", 
              description="紧急修复token过期判断问题")
  得到 t-005

→ startTask("t-005")
   ⏸️ 自动暂停: t-002
   🔄 开始: t-005 (修复登录失效bug)
   
   [修复bug中...]
   
→ completeTask("t-005", result="修复了token过期判断逻辑，添加了刷新机制")
  ✅ 已完成: 修复登录失效bug

→ startTask("t-002")  # 恢复原任务
   ✅ 已恢复: 实现后端JWT验证
   继续之前的工作: 做验证部分...

→ completeTask("t-002", result="完成了JWT验证中间件")
→ removeTask("t-002")
```

#### 场景2: 多任务交替进行

```
[开发-A] 同时进行多个任务

→ createTask("任务A: 设计数据库表")
→ createTask("任务B: 写API文档")
→ createTask("任务C: 配置Docker")

→ startTask("t-001")  # 开始任务A
   [工作中...做到一半]
   
→ startTask("t-002")  # 切换到任务B
   ⏸️ 自动暂停: t-001
   🔄 开始: t-002
   [工作中...]
   
→ startTask("t-001")  # 回来继续任务A
   ⏸️ 自动暂停: t-002
   🔄 恢复: t-001
   [继续之前的工作...]
   
→ completeTask("t-001")
→ removeTask("t-001")
```

#### 场景3: 主动暂停记录进度

```
[开发-A] 今天先做到这里，明天继续

→ pauseTask("t-003", reason="今日工作结束，明日继续")
  ✅ 已暂停: 实现前端登录页面
     当前进度: 完成了登录表单UI，待做表单验证

[第二天]

→ startTask("t-003")
  🔄 恢复: 实现前端登录页面
     上次进度: 完成了登录表单UI，待做表单验证
  
  [继续做表单验证...]
```

---

## 5. 持久化方案

### 5.1 存储路径

```
data/agents/{agent_id}/
└── tasks.jsonl          # 任务数据（JSON Lines格式）
```

### 5.2 存储格式

每行一个 JSON 对象：

```jsonl
{"id": "t-001", "title": "分析页面结构", "status": "done", "created_at": "...", "completed_at": "...", "result": "每页25条", "pause_count": 0}
{"id": "t-002", "title": "实现后端JWT", "status": "paused", "created_at": "...", "started_at": "...", "paused_at": "...", "pause_count": 2}
{"id": "t-003", "title": "实现前端页面", "status": "doing", "created_at": "...", "started_at": "...", "paused_at": null, "pause_count": 0}
```

### 5.3 自动保存策略

- **触发时机**：任何任务状态变更后自动保存
- **写入方式**：全量写入（数据量小，简单可靠）
- **加载时机**：Agent 初始化时自动加载
- **异常处理**：加载失败时创建空任务列表

---

## 6. System Prompt 注入

```markdown
## 任务管理工具

状态流转: todo → doing ↔ paused → done

### 工具说明
- **createTask(title, description?)**: 创建新任务(todo)
- **listTasks(filter?)**: 查看任务列表
- **startTask(task_id)**: 开始/恢复任务，自动暂停之前的任务
- **pauseTask(task_id, reason?)**: 主动暂停当前任务
- **completeTask(task_id, result?)**: 完成任务
- **removeTask(task_id)**: 清理已完成的任务
- **getCurrentTask()**: 获取当前进行中的任务

### 使用原则
1. **及时拆解**：收到复杂任务立即用 createTask 拆解
2. **自动暂停**：startTask 会自动暂停前一个任务，无需手动
3. **主动暂停**：需要临时切换时，可用 pauseTask 记录进度
4. **记录进度**：pause/complete 时记录当前进度，方便恢复
5. **定期清理**：每完成 3-5 个任务就 removeTask 清理

### 典型场景

场景A: 正常执行
用户: "帮我实现XX功能"
→ createTask 拆解 → startTask 开始 → 执行 → completeTask 完成

场景B: 中途插入紧急任务  
用户: "先停一下，帮我修个bug"
→ pauseTask(当前任务, reason="处理紧急bug")
→ createTask(title="修bug") → startTask → 执行 → completeTask
→ startTask(原任务) → 继续执行

场景C: 多任务交替
→ startTask(A) → 工作 → startTask(B) [自动暂停A] → 工作
→ startTask(A) [自动暂停B] → 继续A
```

---

## 7. 实现注意事项

### 7.1 工具注册

在 `tlon/tools/` 目录下创建以下文件（位于**工具层**）：

```
tlon/tools/
├── create_task.py        # CreateTaskTool
├── list_tasks.py         # ListTasksTool
├── start_task.py         # StartTaskTool
├── pause_task.py         # PauseTaskTool
├── complete_task.py      # CompleteTaskTool
├── remove_task.py        # RemoveTaskTool
└── get_current_task.py   # GetCurrentTaskTool
```

任务工具属于**工具层**，被 **Agent 层**的 `ToolExecutor` 调用执行。

### 7.2 与现有系统集成

1. **Agent 初始化**：加载持久化的任务列表
2. **Agent 关闭**：确保任务状态已保存
3. **工具注册**：在 Agent 的 `_register_tools()` 中注册 7 个任务工具

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent 层                             │
│  ┌─────────────────┐  ┌─────────────────────────────────┐  │
│  │  Task Manager   │  │         Tool Executor           │  │
│  │  (子系统层)      │◀─│  ┌─────────────────────────┐   │  │
│  │                 │  │  │  createTask             │   │  │
│  │  - 任务状态管理  │  │  │  listTasks              │   │  │
│  │  - 持久化存储   │  │  │  startTask              │   │  │
│  │                 │  │  │  pauseTask      (工具层) │   │  │
│  └─────────────────┘  │  │  completeTask           │   │  │
└───────────────────────┤  │  removeTask             │   │  │
                        │  │  getCurrentTask         │   │  │
                        │  └─────────────────────────┘   │  │
                        └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 ID 生成策略

使用自增数字格式：`t-{counter}`

- 如：t-001, t-002, t-003...
- 每个 Agent 独立计数
- 从 1 开始，不足 3 位补零

### 7.4 列表长度限制

建议最大任务数：**50 个**

- 超过时创建新任务给出警告
- 提示用户先清理已完成的任务

### 7.5 状态转换校验

```python
# 有效的状态转换
VALID_TRANSITIONS = {
    "todo": ["doing"],                    # 只能开始
    "doing": ["paused", "done"],          # 可以暂停或完成
    "paused": ["doing", "done"],          # 可以恢复或完成
    "done": [],                            # 终态，不能再转
}
```

---

## 8. 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| 1.0 | 2026-03-08 | 初始版本，3状态(todo/doing/done)，6个工具 |
| 1.1 | 2026-03-08 | 增加 paused 状态，新增 pauseTask 工具，startTask 自动暂停而非完成 |

---

## 9. 相关文档

| 文档 | 说明 |
|------|------|
| [../01-architecture/overview.md](../01-architecture/overview.md) | 整体架构设计 |
| [../04-skills/README.md](../04-skills/README.md) | Skills 系统开发指南 |

---

*文档版本: 1.1*  
*最后更新: 2026-03-08*
