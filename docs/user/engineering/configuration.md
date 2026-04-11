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

## Agent 配置 (agents.yaml)

### 文件位置

```
./config/agents.yaml
```

### 完整配置示例

```yaml
agents:
  # 客服 - 主动响应
  - id: "support-001"
    name: "小夏"
    personality: |
      热情、耐心、善于倾听。
      擅长理解用户需求并协调资源。
    proactive: true
    rules:
      - "主动响应用户输入"
      - "初步分类需求"
      - "必要时召唤专家"
    skills:
      - "documentation"
      - "web-search"
    llm:
      model_name: "gpt-4"
      temperature: 0.7
      max_tokens: 2000
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
      cpu_limit: 1.0

  # 开发 - 被动响应
  - id: "dev-001"
    name: "陈代码"
    personality: |
      技术导向，注重代码质量。
      写代码前会思考架构。
    proactive: false
    rules:
      - "只响应@mentions"
      - "可执行bash命令"
      - "优先使用TypeScript"
    skills:
      - "git-operations"
      - "file-operations"
      - "code-linting"
    llm:
      model_name: "gpt-4"
      temperature: 0.3  # 更低温度，更确定
    docker:
      memory_limit: "1g"
      volumes:
        - "./src:/workspace/src"
```

### 字段说明

#### 基础字段

| 字段 | 类型 | 必需 | 说明 |
|-----|------|------|------|
| `id` | string | 是 | 唯一标识符，系统使用 |
| `name` | string | 是 | 显示名称，用户可见 |
| `personality` | string | 否 | 性格描述，影响回复风格 |
| `proactive` | bool | 否 | 是否主动响应 (默认 false) |
| `role.title` | string | 否 | 职务名称（如：开发工程师） |
| `role.jd` | string | 否 | 职责描述 |
| `role.department` | string | 否 | 所属部门 |
| `skills` | list[string] | 否 | ⏳ 描述性标签（旧实现已移除，新实现待重做） |

#### 行为规则

| 字段 | 类型 | 说明 |
|-----|------|------|
| `rules` | list[string] | 行为约束列表 |

常见规则:
- `"只响应@mentions"`
- `"可执行bash命令"`
- `"可修改代码"`
- `"只读模式"`

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
