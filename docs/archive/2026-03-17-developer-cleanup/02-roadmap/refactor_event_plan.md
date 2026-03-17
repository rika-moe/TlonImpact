# Event 系统重构计划

## 目标
将 Event 定义从 `minions.core` 移动到独立的 `minions.protocol` 模块，实现 Server、Agent、ChatRoom、TUI 统一使用同一套 Event 系统。

## 重构原因
1. **消除重复定义**：TUI 当前自定义了 Event 类，违反 DRY 原则
2. **类型一致性**：统一类型后可以做 `isinstance(event, Event)` 检查
3. **解耦架构**：protocol 模块无其他依赖，可被所有组件引用
4. **可维护性**：新增事件类型只需修改一处

## 架构变更

### 重构前
```
minions/
├── core/
│   ├── event.py          # 定义 Event (将被删除)
│   ├── event_bus.py      # 依赖 event.py
│   ├── chatroom.py       # 依赖 event.py
│   └── message.py
├── agents/agent.py       # 依赖 core.event
├── cli/tui_textual.py    # ❌ 自定义 Event (重复定义)
└── server/api/websocket.py # 依赖 core.event
```

### 重构后
```
minions/
├── protocol/              # 新增：共享协议层
│   ├── __init__.py
│   └── event.py           # 统一 Event 定义
├── core/
│   ├── event_bus.py       # 依赖 protocol.event
│   └── chatroom.py        # 依赖 protocol.event
├── agents/agent.py        # 依赖 protocol.event
├── cli/tui_textual.py     # 依赖 protocol.event
└── server/api/websocket.py # 依赖 protocol.event
```

## 详细任务清单

### Phase 1: 创建 protocol 模块

- [x] 1.1 创建 `minions/protocol/__init__.py`
- [x] 1.2 创建 `minions/protocol/event.py`（从 core/event.py 移动 + 增强序列化）
- [x] 1.3 确保 protocol 模块无外部依赖（除了标准库）

### Phase 2: 更新服务端模块导入

- [x] 2.1 修改 `minions/core/event_bus.py`
  - [x] 修改导入: `from ..protocol import Event, EventType`
- [x] 2.2 修改 `minions/core/chatroom.py`
  - [x] 修改导入: `from ..protocol import Event, EventType`
- [x] 2.3 修改 `minions/agents/agent.py`
  - [x] 修改导入: `from ..protocol import Event, EventType`
- [x] 2.4 修改 `minions/agents/agent_registry.py`
  - [x] 修改导入: `from ..protocol import Event, EventType`
- [x] 2.5 修改 `minions/server/api/websocket.py`
  - [x] 修改导入: `from ...protocol import Event, EventType`
  - [x] 使用 `Event.to_dict()` / `Event.from_dict()` 进行序列化
- [x] 2.6 修改 `minions/server/core/server.py`
  - [x] 修改导入: `from ...protocol import Event, EventType`
- [x] 2.7 修改 `minions/server/api/rest.py`
  - [x] 修改导入: `from ...protocol import EventType`
- [x] 2.8 修改 `minions/main.py`
  - [x] 修改导入: `from .protocol import EventType`

### Phase 3: 重构 TUI 模块

- [x] 3.1 修改 `minions/cli/tui_textual.py`
  - [x] 删除本地 `Event` 类定义
  - [x] 添加导入: `from ..protocol import Event, EventType`
  - [x] 创建 `format_event()` 函数用于 TUI 显示
  - [x] 修改事件处理逻辑使用新的 Event 类型
  - [x] 确保时间戳解析兼容（string/float/int）

### Phase 4: 清理旧代码

- [x] 4.1 删除 `minions/core/event.py`
- [x] 4.2 修复 `minions/core/__init__.py` 从 protocol 导入
- [x] 4.3 验证所有 import 路径正确

### Phase 5: 测试验证

- [x] 5.1 静态导入测试（protocol, event_bus 等模块导入成功）
- [x] 5.2 Event 序列化/反序列化测试（to_dict/from_dict 工作正常）
- [x] 5.3 时间戳兼容性测试（ISO 字符串和 Unix 时间戳都能解析）
- [ ] 5.4 运行 `minions server` 启动服务器（需要安装依赖）
- [ ] 5.5 运行 `minions tui` 连接服务器
- [ ] 5.6 测试完整功能流程

## 关键代码变更示例

### 1. protocol/event.py（新增）
```python
"""Event system protocol - shared across all components."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict


class EventType(str, Enum):
    """Event types as string enum for easy JSON serialization."""
    MESSAGE_NEW = "message.new"
    MESSAGE_MENTION = "message.mention"
    # ... 其他类型


@dataclass
class Event:
    """Event definition shared between server and clients."""
    
    type: EventType | str
    payload: Any
    source: str
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON transmission."""
        payload = self.payload
        if hasattr(payload, 'to_dict'):
            payload = payload.to_dict()
        
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, EventType) else self.type,
            "payload": payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Deserialize from dictionary."""
        data = data.copy()
        
        # Convert type string to enum
        type_value = data.get("type")
        if isinstance(type_value, str):
            try:
                data["type"] = EventType(type_value)
            except ValueError:
                data["type"] = type_value
        
        # Convert timestamp
        ts = data.get("timestamp")
        if isinstance(ts, str):
            data["timestamp"] = datetime.fromisoformat(ts)
        elif isinstance(ts, (int, float)):
            data["timestamp"] = datetime.fromtimestamp(ts)
        
        return cls(**data)
```

### 2. TUI 事件处理（修改）
```python
# 修改前：自定义 Event 类
class Event:
    def __init__(self, data: dict): ...

# 修改后：使用 protocol.Event
from ..protocol import Event, EventType

def on_event(self, data: dict):
    event = Event.from_dict(data)
    # 统一使用 event.type, event.payload 等
```

## 风险评估

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 循环导入 | 低 | 高 | protocol 模块保持无外部依赖 |
| 序列化失败 | 中 | 中 | 保留兼容的 timestamp 解析 |
| TUI 显示异常 | 中 | 中 | 保留 format() 方法或创建包装类 |
| 类型不匹配 | 低 | 中 | 使用 `EventType | str` 联合类型 |

## 回滚计划

如果重构失败：
1. 从 git 恢复删除的 `minions/core/event.py`
2. 恢复所有导入路径
3. 恢复 TUI 的本地 Event 类

## 预计时间

- Phase 1: 30 分钟
- Phase 2: 45 分钟
- Phase 3: 45 分钟
- Phase 4: 15 分钟
- Phase 5: 30 分钟

**总计: ~2.5 小时**
