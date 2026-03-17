# Config 系统整理总结

## 变更概述

本次整理解决了 config 系统的重复定义、导入混乱和职责不清问题。

---

## 主要变更

### 1. 删除重复文件

**删除**: `src/tlon/base/config/config.py`

该文件与 `models.py` 重复定义了 `AgentConfig` 和 `DockerRuntimeConfig`，且缺少完整的序列化方法。

### 2. 合并 LLMConfig

**文件**: `src/tlon/base/config/models.py`

在原有字段基础上添加了运行时字段：
- `base_url: str = ""` - API 基础 URL
- `auth_token: str = ""` - 认证令牌
- `max_retries: int = 3` - 最大重试次数

**注意**: 运行时字段不包含在 `to_dict()` 中，不会持久化到 YAML 文件。

### 3. 简化 settings.py

**文件**: `src/tlon/base/settings.py`

**变更前**: 使用多个 dataclass（`LLMConfig`, `DockerConfig`, `MemoryConfig`, `TelegramConfig`）

**变更后**: 简化为扁平属性：
```python
# LLM settings
self.llm_base_url = os.getenv("OPENAI_BASE_URL", "...")
self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
self.llm_model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4")
...
```

新增 `to_llm_config()` 方法用于创建 `LLMConfig` 实例。

### 4. 统一导入路径

所有 config 相关类统一从 `tlon.base.config` 导入：

```python
# 正确
from tlon.base.config import AgentConfig, LLMConfig, DockerRuntimeConfig

# 错误（已删除）
from tlon.base.config.config import AgentConfig

# 错误（settings 不再导出 LLMConfig）
from tlon.base.settings import LLMConfig
```

### 5. 更新 settings 使用方式

| 旧用法 | 新用法 |
|--------|--------|
| `settings.app.data_dir` | `settings.data_dir` |
| `settings.app.log_level` | `settings.log_level` |
| `settings.llm.base_url` | `settings.llm_base_url` |
| `settings.llm.auth_token` | `settings.llm_api_key` |
| `settings.docker.image` | `settings.docker_image` |

---

## 新架构

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Environment Config (环境变量)                       │
│ src/tlon/base/settings.py                                    │
│ - 扁平属性（非 dataclass）                                   │
│ - 提供 to_llm_config() 转换方法                              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: File Config (YAML 配置文件)                         │
│ src/tlon/base/config/models.py                               │
│ - 所有 YAML 配置的数据模型（单一真相源）                      │
│ - AgentConfig, LLMConfig, DockerRuntimeConfig, etc.          │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Runtime Config (运行时配置)                         │
│ src/tlon/subsystem/memory/models.py                          │
│ - MemoryConfig: Agent 级别的内存配置                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 修改的文件列表

### 删除
- `src/tlon/base/config/config.py` ❌

### 修改
- `src/tlon/base/config/models.py` - 添加 LLMConfig 字段，迁移 memory 初始化
- `src/tlon/base/config/__init__.py` - 无需修改（已正确导出）
- `src/tlon/base/settings.py` - 完全重写，移除 dataclass

### 导入更新（11 个文件）
- `src/tlon/agents/__init__.py`
- `src/tlon/agents/agent.py`
- `src/tlon/agents/agent_registry.py`
- `src/tlon/agents/llm_client.py`
- `src/tlon/sandbox/docker_runtime.py`
- `src/tlon/app/server/app.py`
- `src/tlon/app/server/core/server.py`
- `src/tlon/app/wizard/agent_store.py`
- `src/tlon/app/wizard/menus/agent_menu.py`
- `src/tlon/app/wizard/menus/quickstart.py`
- `src/tlon/subsystem/memory/manager.py`
- `src/tlon/app/cli/main.py`
- `src/tests/wizard/test_agent_store.py`
- `src/tests/wizard/test_position_store.py`

---

## 测试验证

```
config 模块测试:    118 passed
wizard 模块测试:     27 passed
全部测试:           237 passed, 4 skipped
```

---

## 迁移指南

对于使用旧代码的地方，请按以下方式更新：

### 1. 更新导入
```python
# 旧
from tlon.base.config.config import AgentConfig
from tlon.base.settings import LLMConfig

# 新
from tlon.base.config import AgentConfig, LLMConfig
```

### 2. 更新 settings 访问
```python
# 旧
settings.llm.base_url
settings.app.data_dir

# 新
settings.llm_base_url
settings.data_dir
```

### 3. 获取 LLMConfig
```python
# 旧
llm_config = settings.llm

# 新
llm_config = settings.to_llm_config()
```

---

*完成时间: 2026-03-13*
