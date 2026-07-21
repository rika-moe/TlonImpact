# 代码风格规范

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **一致性优先**: 优先遵循项目现有风格
2. **工具自动化**: 使用 Black、Ruff 自动化格式化
3. **类型安全**: 严格类型检查，无隐式 Any

---

## 工具配置

### Black（格式化）

```bash
black src/ tests/        # 格式化
black --check src/      # 检查
```

配置 (`pyproject.toml`):
```toml
[tool.black]
line-length = 100
target-version = ["py311"]
```

### Ruff（代码检查）

```bash
ruff check src/ tests/       # 检查
ruff check --fix src/       # 自动修复
```

配置:
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

严格模式:
- `disallow_untyped_defs = true`
- `disallow_incomplete_defs = true`
- `no_implicit_optional = true`

---

## Logging

统一使用 structlog（`from tlon.base.logger import get_logger`），遵循以下规则：

### 事件名

- 英文、小写、snake_case 的**名词性短句**，如 `agent_started`、`message_received`、`session_completed`；
- 不用进行时 / 过去式 / 省略号（`starting agent...` ❌、`agent ready` ❌）；
- 一条逻辑事件只打**一条**日志，禁止把 banner 拆成多条前导空格续行。

### 结构化字段

- **变量一律作为结构化字段**，禁止用 f-string 焊进事件文本：

```python
logger.info("event_queued", event_type=t, queue_size=1)          # ✅
logger.info(f"Event queued: {t} (queue size: {n})")              # ❌
```

- 字段值保持原生类型（list / dict 直接传，structlog 会 JSON 序列化）；错误信息用 `error=str(e)` 字段，堆栈用 `exc_info=True`。

### 组件身份与实例维度

- 组件身份靠 **logger 名（模块名）**：一律 `get_logger(__name__)`，事件文本**不加** `[Component]` 前缀；
- 实例维度（agent_id 等）走 **contextvars**（`bound_contextvars`）或结构化字段，不要拼进 logger 名（`tlon.agents.agent.shen-qingyan` 这种实例 logger 名禁止）。

### 内容类字段（全文记录，不截断）

目标用户是自部署，用户消息、Agent 回复、LLM prompt/completion、tool 参数/返回**全文**记为独立字段，方便 debug。字段名约定：

| 字段名 | 内容 |
|--------|------|
| `content` | 消息全文（用户消息、Agent 回复） |
| `messages` | LLM 请求（system prompt + 对话消息全文） |
| `response` | LLM 返回全文 |
| `arguments` | tool 调用入参 |
| `result` | tool 返回结果 |

不做开关，默认记录；不截断（禁止 `content[:50]`）。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../reference/imports.md](../reference/imports.md) | 导入路径规范 | L3 |

---

## 验证清单

- [x] pyproject.toml 配置有效
- [x] 工具版本锁定
