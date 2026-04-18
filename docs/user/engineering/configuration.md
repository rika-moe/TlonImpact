# 配置说明

> 所有配置项的技术细节。

---

## 环境变量

### 核心变量

| 变量 | 必需 | 默认值 | 说明 |
|-----|------|-------|------|
| `OPENAI_API_KEY` | 是 | - | LLM API Key |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | API 端点 |
| `OPENAI_MODEL_NAME` | 否 | `gpt-4` | 默认模型 |
| `OPENAI_TEMPERATURE` | 否 | `0.7` | 生成温度 (0-1) |
| `DATA_DIR` | 否 | `./data` | 数据存储目录 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |

### Docker 配置

| 变量 | 默认值 | 说明 |
|-----|-------|------|
| `DOCKER_DEFAULT_IMAGE` | `ubuntu:22.04` | Agent 容器镜像 |
| `DOCKER_NETWORK_MODE` | `host` | 网络模式 |

### 内存配置

| 变量 | 默认值 | 说明 |
|-----|-------|------|
| `MEMORY_MAX_HISTORY` | `1000` | 最大历史消息数 |
| `MEMORY_SUMMARY_WINDOW` | `20` | 摘要窗口大小 |

### Telegram 集成 (可选)

| 变量 | 说明 |
|-----|------|
| `TELEGRAM_BOT_TOKEN` | Bot Token |
| `TELEGRAM_CHAT_ID` | 聊天 ID |

---

## Agent 配置

### 文件位置

每个 Agent 一个独立的 YAML 文件：

```
./config/agents/
├── dev-001.yaml
├── pm-001.yaml
└── support-001.yaml
```

### 完整配置示例

```yaml
agent:
  id: "dev-001"
  name: "林知遥"

  identity: |
    你是一名技术能力强的开发工程师，注重细节，喜欢钻研。
    你的习惯是先通过 bash 命令探查环境，再给出解决方案。

  profile: |
    开发工程师，技术部。负责代码编写、Bug 修复和技术方案实现。
    有技术问题或 Bug 需要处理时可以找我。

  behavior: |
    收到直接@时，必须响应。
    未被@时，观察对话内容决定是否参与讨论。
    被@时的工作流程：
    1. 分析问题背景和需求
    2. 制定解决思路
    3. 如需验证：调用 bash/python 工具
    4. 给出完整、准确的回复

  proactive: false

  llm:
    model_name: "kimi-k2.5"
    temperature: 1.0

  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

### 字段说明

#### 基础字段

| 字段 | 类型 | 必需 | 说明 |
|-----|------|------|------|
| `id` | string | 是 | 唯一标识符，系统使用 |
| `name` | string | 是 | 显示名称，用户可见 |
| `identity` | string | 否 | 自我身份：你是谁、性格特点 |
| `profile` | string | 否 | 公开档案：职责、能力、何时可以@你（队友可见） |
| `behavior` | string | 否 | 行为守则：工作流程、响应规则 |
| `proactive` | bool | 否 | 是否主动响应 (默认 false) |

#### LLM 配置

| 字段 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `model_name` | string | `gpt-4` | 模型名称 |
| `temperature` | float | `0.7` | 创造性 (0=保守, 1=创意) |
| `max_tokens` | int | `2000` | 最大生成token数 |

#### Docker 配置

| 字段 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `image` | string | `ubuntu:22.04` | 容器镜像 |
| `memory_limit` | string | `512m` | 内存限制 |
| `cpu_limit` | float | `1.0` | CPU限制 |
| `volumes` | list[string] | `[]` | 额外挂载卷 |

---

## ~~Skill 配置~~

> ❌ **Skill 系统已于 2026-03-17 移除**
>
> 原 Skill 配置已失效。如需扩展 Agent 能力，请直接通过 `executeBashCommand` 执行命令。

### Frontmatter 字段

| 字段 | 必需 | 说明 |
|-----|------|------|
| `name` | 是 | Skill 标识符 |
| `description` | 是 | 简短描述 |

---

## 运行时配置

### 服务端端口

```bash
minions server \
  --host 127.0.0.1 \
  --rest-port 8080 \
  --ws-port 8888
```

### 客户端连接

```bash
tlon tui \
  --host 127.0.0.1 \
  --port 8888 \
  --rest-port 8080
```

---

## 数据存储

### Agent 数据目录

```
data/agents/{agent-id}/
├── memory/
│   ├── working.json      # 工作记忆 (最近10条)
│   ├── episodes.json     # 情节记忆 (会话摘要)
│   └── facts.json        # 事实库 (结构化数据)
└── state.json            # 运行时状态
```

### 记忆文件格式

**working.json:**
```json
{
  "entries": [
    {
      "timestamp": "2024-03-09T10:23:45Z",
      "role": "user",
      "content": "优化API"
    },
    {
      "timestamp": "2024-03-09T10:23:46Z",
      "role": "agent",
      "content": "收到",
      "tool_calls": [...]
    }
  ]
}
```

**facts.json:**
```json
{
  "facts": [
    {
      "id": "pref-001",
      "type": "preference",
      "key": "frontend_framework",
      "value": "React",
      "created_at": "2024-03-01T12:00:00Z"
    }
  ]
}
```

---

## 配置验证

```bash
# 使用 wizard 验证
tlon wizard

# 手动检查 (Python)
python -c "from minions.settings import settings; print(settings.llm.model_name)"
```
