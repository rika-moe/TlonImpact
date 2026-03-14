# Config 重构实施路线图

## 状态：✅ 已完成

## 变更摘要

### 核心变更

#### 1. 统一 Position 模型 ✅
- **文件**: `src/tlon/organization/positions/models.py`
- **变更**: `Position` 类变为 `PositionConfig` 的别名
- **优势**: 单一数据源，消除重复定义

#### 2. Wizard Store 使用 Config 层 ✅
- **文件**: 
  - `src/tlon/app/wizard/position_store.py`
  - `src/tlon/app/wizard/agent_store.py`
- **变更**: 重写为 `ConfigLoader`/`ConfigSaver` 的包装器
- **优势**: 消除重复的 YAML 加载/保存逻辑

#### 3. 清理验证器 ✅
- **文件**: `src/tlon/app/wizard/validators.py`
- **变更**: 
  - 移除与 `ConfigValidator` 重复的逻辑
  - 添加 `InputValidator` 用于界面输入验证
  - 保留 `EnvironmentChecker` 和 `APIValidator`
  - 重新导出 `ConfigValidator` 以保持向后兼容

#### 4. 默认模板使用 PositionConfig ✅
- **文件**: `src/tlon/app/wizard/position_store.py`
- **变更**: `DEFAULT_POSITIONS` 使用 `PositionConfig` 对象而非字典
- **优势**: 类型安全，统一数据模型

### 架构现状

```
┌─────────────────────────────────────────────────────────────┐
│  Config Layer (base/config/) - 单一数据源 ✅                  │
│  ├── models.py: PositionConfig, AgentConfig                 │
│  ├── loader.py: ConfigLoader                                │
│  ├── saver.py: ConfigSaver                                  │
│  └── validator.py: ConfigValidator                          │
├─────────────────────────────────────────────────────────────┤
│  Organization Layer (organization/positions/)                 │
│  ├── models.py: Position = PositionConfig (别名) ✅          │
│  └── registry.py: PositionRegistry (使用 ConfigLoader) ✅    │
├─────────────────────────────────────────────────────────────┤
│  Wizard Layer (app/wizard/)                                  │
│  ├── position_store.py: PositionStore (Config包装器) ✅      │
│  ├── agent_store.py: AgentStore (Config包装器) ✅            │
│  └── validators.py: 输入验证 + 环境检查 ✅                   │
└─────────────────────────────────────────────────────────────┘
```

### 向后兼容性

#### 导入兼容 ✅
```python
# 旧的导入仍然有效
from tlon.organization.positions.models import Position

# 新的推荐导入
from tlon.base.config.models import PositionConfig
# Position == PositionConfig
```

#### 方法兼容 ✅
```python
# PositionStore.get_default_templates() 改名为 get_default_positions()
# 但保留了向后兼容的别名
```

### 测试状态

- ✅ Config 测试: 118 个通过
- ✅ Wizard 测试: 27 个通过
- ✅ 全部测试: 237 个通过, 4 个跳过

### 文件变更清单

#### 修改的文件
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `organization/positions/models.py` | 重写 | Position 变为 PositionConfig 别名 |
| `organization/positions/registry.py` | 修改 | 使用 ConfigLoader/Saver |
| `app/wizard/position_store.py` | 重写 | 使用 ConfigLoader/Saver |
| `app/wizard/agent_store.py` | 重写 | 使用 ConfigLoader/Saver |
| `app/wizard/validators.py` | 修改 | 清理重复验证逻辑 |
| `app/server/serverapp.py` | 修改 | 使用 ConfigLoader 加载 agents/positions |
| `app/cli/main.py` | 修改 | 使用 ConfigLoader/Saver 进行 CLI 操作 |
| `tests/wizard/test_position_store.py` | 修改 | 修复测试数据和方法名 |

#### 删除的冗余代码
- `serverapp.py`: `_load_agents_from_directory()` - 手动遍历 YAML 文件
- `serverapp.py`: `_build_agent_config()` - 手动构建 AgentConfig 对象  
- `cli/main.py`: `_mirror_reflect()` - 手动 YAML 读取，改用 `ConfigLoader`
- `cli/main.py`: `_mirror_destroy()` - 直接文件操作，改用 `ConfigSaver.delete_agent()`
- `cli/main.py`: `_mirror_create()` - 手动 YAML 读取/写入，改用 `ConfigLoader/ConfigSaver`
- `cli/main.py`: `_mirror_edit()` - 手动文件路径构造，改用 `ConfigLoader`

### 验证命令

```bash
# 运行所有测试
.venv/bin/python -m pytest src/tests/ -v

# 验证核心导入
.venv/bin/python -c "
from tlon.base.config import ConfigLoader, ConfigSaver, ConfigValidator
from tlon.base.config import AgentConfig, PositionConfig
from tlon.organization.positions.models import Position
from tlon.app.wizard import PositionStore, AgentStore
print('All imports OK')
"
```

---

*重构完成日期: 2026-03-14*
