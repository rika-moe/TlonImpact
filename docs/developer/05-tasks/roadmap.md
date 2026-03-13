# Task Manager 开发路线图

> 功能: Agent Personal Task Manager  
> 版本: 1.1  
> 预计工期: 1-2 天

---

## 目标

实现一套任务管理工具，让 Agent 能够：
- 将大任务拆解成小任务
- 跟踪任务状态（todo/doing/paused/done）
- 支持暂停/恢复任务
- 持久化存储任务状态

---

## Phase 1: 核心数据模型 (2-3 小时)

### 1.1 Task 模型
- [ ] 创建 `minions/tasks/__init__.py`
- [ ] 创建 `minions/tasks/models.py`
  - Task dataclass
  - TaskStatus 枚举
  - 状态流转方法

### 1.2 TaskManager 管理器
- [ ] 创建 `minions/tasks/manager.py`
  - TaskManager 类
  - 内存存储
  - ID 生成器

**验收标准:**

```python
from tlon.subsystem.tools.tasks import Task, TaskManager

manager = TaskManager(agent_id="dev-001")
task = manager.create_task("实现登录API")
assert task.status == "todo"
assert task.id == "t-001"
```

---

## Phase 2: 持久化层 (1-2 小时)

### 2.1 JSONL 存储
- [ ] 实现 `save()` 方法
- [ ] 实现 `load()` 方法
- [ ] 自动保存触发机制

**验收标准:**
```python
manager.create_task("任务1")
manager.save()

# 重启后
manager2 = TaskManager(agent_id="dev-001")
manager2.load()
assert len(manager2.list_tasks()) == 1
```

---

## Phase 3: 工具实现 (3-4 小时)

### 3.1 创建任务工具
- [ ] `minions/tools/create_task.py`
- [ ] 参数: title, description
- [ ] 返回: task_id, status

### 3.2 查看任务工具
- [ ] `minions/tools/list_tasks.py`
- [ ] 参数: filter
- [ ] 返回: 任务列表

### 3.3 开始任务工具
- [ ] `minions/tools/start_task.py`
- [ ] 自动暂停当前 doing 任务
- [ ] 状态流转: todo/paused → doing

### 3.4 暂停任务工具 ⭐
- [ ] `minions/tools/pause_task.py`
- [ ] 参数: task_id, reason(optional)
- [ ] 状态流转: doing → paused

### 3.5 完成任务工具
- [ ] `minions/tools/complete_task.py`
- [ ] 参数: task_id, result
- [ ] 计算耗时

### 3.6 移除任务工具
- [ ] `minions/tools/remove_task.py`
- [ ] 约束: 只能移除 done 任务

### 3.7 获取当前任务工具
- [ ] `minions/tools/get_current_task.py`
- [ ] 返回 doing 或最近 paused 的任务

**验收标准:**
```python
# 每个工具都能独立测试
result = await create_task_tool.execute(title="测试任务")
assert result.success
assert result.data["status"] == "todo"
```

---

## Phase 4: Agent 集成 (1-2 小时)

### 4.1 初始化集成
- [ ] Agent 初始化时加载 TaskManager
- [ ] Agent 关闭时保存任务

### 4.2 工具注册
- [ ] 在 `agent.py` 的 `_register_tools()` 中注册 7 个工具

### 4.3 System Prompt
- [ ] 在 system prompt 中添加任务管理工具说明

**验收标准:**
```python
# Agent 能调用任务工具
agent = Agent(...)
await agent.initialize()
assert "createTask" in agent.tools
assert "pauseTask" in agent.tools
```

---

## Phase 5: 测试与验证 (2-3 小时)

### 5.1 单元测试
- [ ] `tests/tasks/test_models.py`
- [ ] `tests/tasks/test_manager.py`
- [ ] `tests/tools/test_task_tools.py`

### 5.2 集成测试
- [ ] 完整工作流测试
- [ ] 持久化测试
- [ ] 多任务切换测试

### 5.3 手动验证
- [ ] 启动 server
- [ ] TUI 连接
- [ ] 实际对话测试

**验收标准:**
```
用户: @开发-A 帮我实现登录功能
开发-A: 好的，我来拆解任务...
      [使用 createTask 创建 4 个任务]
      [使用 startTask 开始第一个]
      ...
用户: 先停一下，帮我修个bug
开发-A: [使用 pauseTask 暂停当前]
      [使用 createTask 创建 bug 任务]
      [使用 startTask 开始 bug 任务]
      ...
```

---

## 依赖关系

```
Phase 1 (模型)
    ↓
Phase 2 (持久化)
    ↓
Phase 3 (工具) ← 并行 → Phase 4 (集成)
    ↓
Phase 5 (测试)
```

---

## 文件清单

### 新增文件
```
minions/
├── tasks/
│   ├── __init__.py
│   ├── models.py
│   └── manager.py
└── tools/
    ├── create_task.py
    ├── list_tasks.py
    ├── start_task.py
    ├── pause_task.py
    ├── complete_task.py
    ├── remove_task.py
    └── get_current_task.py

tests/
└── tasks/
    ├── test_models.py
    └── test_manager.py
```

### 修改文件
```
minions/
├── agents/
│   └── agent.py          # 注册工具
└── tools/
    └── __init__.py       # 导出工具
```

---

## 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 持久化格式变更 | 中 | 使用简单JSONL，预留版本字段 |
| 任务ID冲突 | 低 | 每个Agent独立计数器 |
| 并发修改 | 低 | 单Agent单线程，无需锁 |
| 列表过长 | 低 | 限制50个，提醒清理 |

---

## 里程碑检查点

### CheckPoint 1: 模型完成
- [ ] Task 模型定义完成
- [ ] TaskManager 基础功能完成
- [ ] 可通过简单测试

### CheckPoint 2: 工具完成
- [ ] 7个工具全部实现
- [ ] 每个工具可独立测试
- [ ] 状态流转正确

### CheckPoint 3: 集成完成
- [ ] Agent 可加载 TaskManager
- [ ] 工具注册完成
- [ ] System Prompt 注入

### CheckPoint 4: 验收通过
- [ ] 完整工作流测试通过
- [ ] 持久化测试通过
- [ ] TUI 手动测试通过

---

## 下一步行动

1. **立即开始**: Phase 1 - 创建数据模型
2. **预计完成**: 1-2 天内完成全部功能
3. **测试验证**: 完成后实际对话测试

---

*创建时间: 2026-03-08*  
*预计工期: 1-2 天*
