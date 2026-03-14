# Config 系统整理路线图

## 目标
清理重复、错误、杂乱的 config 代码，建立清晰的三层架构：
- Layer 1: Environment Config（环境变量）
- Layer 2: File Config（YAML 配置）
- Layer 3: Runtime Config（运行时配置）

---

## Phase 1: 删除重复的 config.py

**目标**: 删除 `src/tlon/base/config/config.py`，将其功能合并到 `models.py`

### 任务清单
- [ ] 将 `config.py` 中的 `__post_init__` 逻辑迁移到 `models.py`
  - `AgentConfig.__post_init__` 需要初始化 memory
- [ ] 删除 `src/tlon/base/config/config.py`
- [ ] 更新 `src/tlon/base/config/__init__.py` 的导入
- [ ] 验证所有引用此文件的代码

### 影响范围
```
src/tlon/base/config/config.py          # 删除
src/tlon/base/config/__init__.py        # 修改导入
src/tlon/agents/__init__.py             # 可能修改
src/tlon/agents/agent.py                # 可能修改
src/tlon/agents/agent_registry.py       # 可能修改
src/tlon/agents/llm_client.py           # 可能修改
src/tlon/sandbox/docker_runtime.py      # 可能修改
src/tlon/app/server/core/server.py      # 可能修改
src/tlon/app/server/app.py              # 可能修改
src/tlon/app/wizard/agent_store.py      # 可能修改
src/tlon/app/wizard/menus/agent_menu.py # 可能修改
src/tlon/app/wizard/menus/quickstart.py # 可能修改
```

---

## Phase 2: 整理 settings.py

**目标**: 移除 dataclass 定义，简化为纯环境变量配置

### 任务清单
- [ ] 移除 `LLMConfig` dataclass → 改为 `get_llm_config()` 函数返回字典
- [ ] 移除 `DockerConfig` dataclass → 改为 `get_docker_config()` 函数返回字典
- [ ] 移除 `MemoryConfig` dataclass → 改为 `get_memory_config()` 函数返回字典
- [ ] 移除 `TelegramConfig` dataclass → 改为 `get_telegram_config()` 函数返回字典
- [ ] 简化 `Settings` 类，只保留必要的环境变量读取
- [ ] 保留 `get_project_root()`, `get_default_data_dir()`, `get_default_config_dir()`

### 新的 settings.py 结构
```python
class Settings:
    """Application settings from environment variables."""
    
    def __init__(self):
        self.app_name = os.getenv("APP_NAME", "Tlon")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.data_dir = Path(os.getenv("DATA_DIR", get_default_data_dir()))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        
        # LLM settings (flat, not nested)
        self.llm_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
        self.llm_model = os.getenv("OPENAI_MODEL_NAME", "gpt-4")
        
        # Docker settings
        self.docker_image = os.getenv("DOCKER_DEFAULT_IMAGE", "ubuntu:22.04")
        
        # ... etc
    
    def to_llm_config(self) -> "LLMConfig":
        """Create LLMConfig from settings."""
        from tlon.base.config import LLMConfig
        return LLMConfig(...)
```

---

## Phase 3: 统一 LLMConfig

**目标**: 合并 `settings.py` 和 `models.py` 中的 `LLMConfig`

### 任务清单
- [ ] 在 `models.py` 的 `LLMConfig` 中添加缺失字段
  - `base_url: str = ""`
  - `auth_token: str = ""`
  - `max_retries: int = 3`
- [ ] 更新 `__post_init__` 从环境变量读取（可选）
- [ ] 更新 `to_dict()` 和 `from_dict()`
- [ ] 更新所有使用 `settings.llm` 的地方

### 字段对比
| 字段 | models.py | settings.py | 决策 |
|------|-----------|-------------|------|
| model_name | ✅ | ✅ | 保留 |
| temperature | ✅ | ✅ | 保留 |
| max_tokens | ✅ | ❌ | 添加默认值 4096 |
| top_p | ✅ | ❌ | 保留 |
| base_url | ❌ | ✅ | **添加** |
| auth_token | ❌ | ✅ | **添加** |
| max_retries | ❌ | ✅ | **添加** |
| base_delay | ❌ | ✅ | 不添加（移到别处） |

---

## Phase 4: 更新导入路径

**目标**: 确保所有代码从 `tlon.base.config` 导入 config 类

### 需要更新的文件
```
src/tlon/agents/llm_client.py           # from tlon.base.config.config → from tlon.base.config
src/tlon/subsystem/memory/manager.py      # from tlon.base.settings → from tlon.base.config
src/tlon/subsystem/memory/summarizer.py   # from tlon.agents.llm_client import LLMConfig → from tlon.base.config
src/tlon/app/server/core/server.py        # from tlon.base.config.config → from tlon.base.config
src/tlon/app/server/app.py                # 检查导入
src/tlon/app/wizard/agent_store.py        # from tlon.base.config.config → from tlon.base.config
src/tlon/app/wizard/menus/agent_menu.py   # 检查导入
src/tlon/app/wizard/menus/quickstart.py   # 检查导入
```

---

## Phase 5: 运行测试验证

### 测试清单
- [ ] `pytest tests/config/` - config 模块测试
- [ ] `pytest tests/wizard/` - wizard 测试
- [ ] `pytest tests/` - 全部测试
- [ ] 手动验证：`python -c "from tlon.base.config import *; print('OK')"`

---

## 依赖关系图

```
Phase 1 (删除 config.py)
    │
    ▼
Phase 2 (整理 settings.py)
    │
    ▼
Phase 3 (统一 LLMConfig)
    │
    ▼
Phase 4 (更新导入)
    │
    ▼
Phase 5 (测试验证)
```

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 循环导入 | 中 | 高 | 使用 TYPE_CHECKING，延迟导入 |
| 测试失败 | 中 | 中 | 逐步修改，每步运行测试 |
| 运行时错误 | 低 | 高 | 完整的集成测试 |

---

*创建时间: 2026-03-13*
*状态: 等待审批*
