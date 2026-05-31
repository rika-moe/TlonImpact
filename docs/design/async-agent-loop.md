# Agent 异步协作设计

> 状态: ✅ 已验证
> 最后验证: 2026-05-31

## 核心理念

1. **Session 状态机**：每个对话是一个独立的 ACTIVE → WAITING → COMPLETED 状态机
2. **步进调度**：主循环每轮推进每个 Session 至多一步，不阻塞在工具等待上
3. **工具后台执行**：工具调用通过 `asyncio.create_task` 派发，Agent 在此期间可处理其他 Session
4. **冲突检测**：`sendMessage(is_complete=true)` 不得与其他工具同时调用
5. **异常安全**：所有工具异常转换为 `ToolResult(success=False)`，不传播到主循环

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [agents.md](./agents.md) | Agent 系统总体设计 | L2 |
| [memory.md](./memory.md) | 记忆系统 | L1 |
| [../architecture/dream.md](../architecture/dream.md) | dream 进程架构 | L2 |
| [../architecture/domains/agents.md](../architecture/domains/agents.md) | Agent 层架构详细 | L3 |

---

## 问题

传统 ReAct loop 是串行模型：

```
一条消息 → 10 轮迭代 → LLM think → 等工具 → LLM think → 等工具 → ... → is_complete
                                                                                    ↓
                                                                            下一条消息
```

无法支持：
- Agent 一边跑 bash 一边回复用户"正在做"
- Agent A 给 Agent B 发消息后不等回复继续干活
- 同时持有多个对话上下文（和用户聊 + 和其他 Agent 聊）

---

## 核心抽象：Session

一个 Session 代表一个"进行中的对话"。

```
Session(id="session-001")
├── context: list[dict]          ← 消息历史（LLM 对话上下文）
├── pending_tasks: dict[str, asyncio.Task]  ← 后台工具调用
├── state: SessionState
│   ├── ACTIVE    ← 可以被 LLM 思考
│   ├── WAITING   ← 等工具结果
│   └── COMPLETED ← 会话结束，可清理
├── created_at, last_active_at  ← 用于调度
└── trigger_content              ← 触发消息内容
```

**创建**：收到新消息 → 新 Session

**结束**：LLM 调用 `sendMessage(is_complete=true)` → `mark_completed()` → 保存记忆 → 下一轮清理

---

## 主循环

```
while self._worker_running:
    1. 接收新消息 → 创建 Session
    2. ACTIVE Session → 一次 think + dispatch（_step_session）
    3. WAITING Session → 收集已完成工具（_collect_session_results）
    4. COMPLETED Session → 清理
    5. 让出控制权
```

```python
async def _main_loop(self):
    sessions: dict[str, AgentSession] = {}
    session_messages: dict[str, Message] = {}

    while self._worker_running:
        # 1. 新消息
        try:
            msg = self._message_queue.get_nowait()
            sid = f"{self.id}-{msg.id}"
            sessions[sid] = AgentSession(sid, msg.content)
            session_messages[sid] = msg
        except asyncio.QueueEmpty:
            if not sessions:
                try:
                    msg = await asyncio.wait_for(self._message_queue.get(), timeout=0.5)
                    sid = f"{self.id}-{msg.id}"
                    sessions[sid] = AgentSession(sid, msg.content)
                    session_messages[sid] = msg
                except TimeoutError:
                    continue

        # 2-3. 推进 Session
        for sid in list(sessions.keys()):
            session = sessions[sid]
            try:
                if session.state == SessionState.WAITING:
                    await self._collect_session_results(session)
                elif session.state == SessionState.ACTIVE:
                    await self._step_session(session, session_messages[sid])
            except Exception:
                session.mark_completed()

            # 4. 清理
            if session.is_completed:
                del sessions[sid]
                session_messages.pop(sid, None)

        await asyncio.sleep(0.01 if sessions else 0.05)
```

---

## 单步执行（_step_session）

每次调用完成一个 ReAct 步骤：think → 派发工具。不等待工具完成。

```
_step_session(session, message):
    if 首次调用:
        构建上下文（记忆 + 对话历史 + 当前消息）

    if 超过最大迭代次数:
        发送超时消息 → COMPLETED

    think()  → LLM 响应

    if LLM 异常:
        递增错误计数 → advance_iteration()
        if 超过最大错误次数: COMPLETED

    if 无工具调用:
        追加 no-tool 提示 → advance_iteration()

    if sendMessage(is_complete=true) + 其他工具:
        冲突检测 → 追加提示 → advance_iteration()

    派发工具:
        for each tc in response.tool_calls:
            asyncio.create_task(_execute_one_tool(tc))
            emit TOOL_EXECUTE_START
        session.state = WAITING
```

---

## 结果收集（_collect_session_results）

工具完成后回收到 Session，逐个处理。

```
_collect_session_results(session):
    completed = session.collect_completed()
    if not completed: return  # 还在等

    for tc, result in completed:
        emit TOOL_EXECUTE_END
        if result.error: emit TOOL_ERROR

        if tc 是 sendMessage 且成功:
            发送消息
            if is_complete: mark COMPLETED

        追加到 context:
            assistant_msg + tool_result_msg

    if is_complete:
        保存到记忆 → session.mark_completed()
    elif 所有工具都完成了:
        advance_iteration() → session.mark_active()
```

---

## 工具执行（_execute_one_tool）

每个工具作为独立的 `asyncio.create_task` 运行：

```python
async def _execute_one_tool(self, tc):
    try:
        return tc, await asyncio.wait_for(
            self.tool_executor.execute(tc.function_name, tc.arguments),
            timeout=self._per_tool_timeout,
        )
    except TimeoutError:
        return tc, ToolResult(success=False, error=f"Timeout after {self._per_tool_timeout}s")
    except Exception as e:
        return tc, ToolResult(success=False, error=str(e))
```

所有异常转换为 `ToolResult(success=False)`，不向上传播。

---

## 跨 Agent 通信

```
Agent A 主循环:
  Session-A1: 和用户对话
    → think → sendMessage(content="B 帮我查一下", mentions=["agent-b"])
    → 工具完成 → advance_iteration → ACTIVE（继续思考）

Agent B 收到消息:
  → 放入 _message_queue
  → 主循环取出 → 创建 Session-B1
  → think → execute → sendMessage(content="查到了", mentions=["agent-a"])
  → is_complete=true → Session-B1 COMPLETED

Agent A 收到回复:
  → 放入 _message_queue
  → 主循环取出 → 关联到 Session-A1
  → context 追加回复 → think → 继续
```

---

## 背景工具（Background Bash）

通过 `ExecuteBashTool` 的 `background: bool` 参数实现：

```
用户: "帮我装依赖"
Agent: think → executeBashCommand("npm install", background=True)
  → 返回 task_id，不等待
  → think → sendMessage("正在安装，稍等")
  → 下一轮 think → checkBashTask(task_id) → 获取结果
```

`CheckBashTaskTool` 查询后台任务状态（pending/running/completed/failed）。

---

## 涉及组件

| 组件 | 作用 |
|------|------|
| `agents/agent.py` | 主循环、步进执行、结果收集 |
| `agents/session.py` | Session 状态机、工具结果管理 |
| `tools/shell/execute_bash.py` | `background` 参数 + 后台执行 |
| `tools/shell/check_bash_task.py` | 查询后台 bash 任务状态 |

---

## 边界情况

| 场景 | 处理 |
|------|------|
| Session 累积过多 | Session 完成后立即清理，不会无限增长 |
| 工具长时间不返回 | `wait_for` 超时降级为 ToolResult(success=False) |
| 用户发新消息打断当前工作 | 新消息入队，下一轮主循环处理 |
| Agent 同时 thinking 和等工具 | Session 状态机保证（同一 Session 不会同时 ACTIVE + WAITING） |
| LLM 连续异常 | `error_count` 超限后发送错误消息并结束 Session |
| 上下文重复构建 | `if not session.context` 守卫，异常后不重建 |

---

*最后更新: 2026-05-31*

## 验证清单

- [x] AgentSession 状态机实现与文档一致（`session.py`）
- [x] `_main_loop` 正确调度 ACTIVE/WAITING/COMPLETED 状态
- [x] `_step_session` 单步执行（think + dispatch），不阻塞等待工具
- [x] `_collect_session_results` 收集后台工具结果，处理 sendMessage
- [x] 所有工具异常不传播到主循环（`_execute_one_tool` 捕获全部）
- [x] 后台 bash 通过 `background: bool` 参数 + `CheckBashTaskTool` 实现
- [x] 175 个 agent 相关测试全部通过
