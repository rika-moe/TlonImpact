# 配置系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-04-19 | 更新: 2026-06-01 (per-agent 布局)

---

## 核心理念

1. **显式配置**: 所有配置通过 YAML 文件，无隐藏默认值
2. **Narrative 驱动**: `identity`/`profile`/`behavior` 各是一段自由文本，非字段集合
3. **分层加载**: 系统配置 → Agent 配置
4. **环境覆盖**: 敏感信息通过环境变量注入
5. **验证优先**: 启动时验证配置，拒绝无效配置
6. **Per-agent 布局**: 一个 agent = 一个目录，隔离存储，备份/迁移友好

---

## 配置分层

```
system (.env) → agent (yaml)
```

| 层级 | 位置 | 内容 |
|------|------|------|
| 系统 | `.env` | API Key、日志级别 |
| Agent | `~/.tlon/{id}/config/agent.yaml` | identity、profile、behavior、LLM 参数、Docker 配置 |

`.env` 加载语义：只加载仓库根目录的 `.env`（与运行时 cwd 无关），且真实环境变量永远优先——`.env` 仅补齐环境中未设置的变量（`override=False`）。

系统层可观测性环境变量（均可选）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `TLON_TRACE_SAMPLE_RATIO` | 1.0（全采样） | [0, 1] 浮点，切换为 TraceIdRatioBased 采样 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 无 | OTLP collector 基础 URL；需 `uv sync --extra otlp` 安装可选依赖，未安装则告警跳过 |
| `TLON_LOG_RETENTION_DAYS` | 7 | server 日志文件 sink（`shared/logs/server-{date}.jsonl`）的按天滚动保留天数 |

---

## 文件系统布局

```
~/.tlon/
├── shared/
│   ├── chat_history/
│   │   └── messages.jsonl
│   ├── traces/
│   │   └── trace-{uuid}.jsonl
│   └── logs/
│       └── server-{date}.jsonl
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml
│   │   ├── IDENTITY.md
│   │   ├── SOUL.md
│   │   └── RULES.md
│   ├── memory/
│   │   ├── config.json
│   │   ├── working/
│   │   │   └── interactions.jsonl
│   │   └── episodic/
│   │       ├── summaries.jsonl
│   │       └── facts.jsonl
│   ├── tasks.jsonl
│   └── workspace/          ← Docker volume mount → /workspace/
└── ...
```

| 目录 | 用途 | 访问者 |
|------|------|--------|
| `shared/` | 跨 agent 共享数据（聊天记录、trace、server 日志） | MessageStore, TraceStore, 日志文件 sink |
| `{id}/config/` | Agent YAML 配置 | ConfigService, Wizard |
| `{id}/memory/` | 双层记忆 (working + episodic) | MemoryManager (host 独占) |
| `{id}/tasks.jsonl` | 任务持久化 | TaskManager (host 独占) |
| `{id}/workspace/` | Agent 工具读写的工作区 | Docker 容器内 Agent (唯一挂载的目录) |

**路径集中管理**: 所有路径通过 `tlon.base.paths.TlonPaths` 解析，其他模块零硬编码字符串。测试使用 `TlonPaths(home=tmpdir)` 隔离。

```python
from tlon.base.paths import paths
paths.agent_config_file("dev-001")  # ~/.tlon/dev-001/config/agent.yaml
```

---

## 关键字段

```yaml
# Agent 配置
agent:
  id: "dev-001"           # 唯一标识（系统路由键）
  name: "林知遥"           # 显示名称

  identity: |             # 自我身份：你是谁、性格特点
    你是一名技术能力强的开发工程师...

  profile: |              # 公开档案：职责、能力、何时可以@你
    开发工程师，技术部。负责代码编写...

  behavior: |             # 行为守则：工作流程、响应规则
    收到@时响应。被@时先分析...

  llm:
    model_name: "gpt-4"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [narrative-prompt.md](./narrative-prompt.md) | Prompt 架构设计（narrative-driven） | L2 |
| [architecture/domains/base.md](../architecture/domains/base.md) | 基础层架构 | L2 |
| [../reference/imports.md](../reference/imports.md) | 导入路径 | L3 |

---

## 验证清单

- [x] 配置字段与代码一致（`identity`/`profile`/`behavior`）
- [x] `RoleConfig` 已移除，role 信息合并入 narrative 文本
- [x] YAML 格式有效
- [x] 环境变量可覆盖
- [x] Per-agent 目录布局已实施
- [x] TlonPaths 集中路径解析已实施
- [x] 测试隔离不污染 ~/.tlon/
