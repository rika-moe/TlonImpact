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
    position: "support"
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
    position: "developer"
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
| `position` | string | 是 | 角色类型 (见下表) |
| `personality` | string | 否 | 性格描述，影响回复风格 |
| `proactive` | bool | 否 | 是否主动响应 (默认 false) |

#### Position 类型

| 类型 | 职责 | 典型 proactive |
|-----|------|--------------|
| `support` | 客服接待 | true |
| `project_manager` | 项目管理 | false |
| `product_manager` | 产品分析 | false |
| `developer` | 代码开发 | false |
| `devops` | 运维部署 | false |

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

## Skill 配置

### 目录结构

```
skills/
└── {skill-name}/
    ├── SKILL.md           # 必需: 技能定义
    ├── scripts/           # 可选: 可执行脚本
    │   └── script.py
    ├── references/        # 可选: 参考文档
    │   └── guide.md
    └── assets/            # 可选: 模板资源
        └── template.json
```

### SKILL.md 格式

```markdown
---
name: git-operations
description: Git 版本控制操作
---

# Git Operations

## 什么时候使用
需要执行 Git 命令时。

## 如何使用
1. 使用 scripts/ 下的脚本
2. 或执行原生 git 命令

## 可用脚本
- `scripts/commit.sh`: 提交变更
  参数: -m "message"

## 示例
```bash
./skills/git-operations/scripts/commit.sh -m "update"
```
```

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
