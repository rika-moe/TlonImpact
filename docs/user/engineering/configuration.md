# 配置说明

> 所有配置项的技术细节。

---

## 环境变量

### 核心变量

| 变量 | 必需 | 默认值 | 说明 |
|-----|------|-------|------|
| `OPENAI_API_KEY` | 是 | - | LLM API Key |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | API 端点 |
| `OPENAI_MODEL_NAME` | 否 | `kimi-k2.5` | 默认模型 |
| `OPENAI_TEMPERATURE` | 否 | `1.0` | 生成温度 (0-2) |
| `TLON_HOME` | 否 | `~/.tlon` | 数据根目录（配置/记忆/邮箱/日志都在其下） |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |

默认值以 `src/tlon/base/settings.py` 为准。

### Docker 与记忆配置

Docker、记忆等 per-agent 配置不在环境变量里，而在各 Agent 的
`~/.tlon/{agent-id}/config/agent.yaml`（见下文 Agent 配置）。

---

## Agent 配置

### 文件位置

每个 Agent 一个家目录，配置集中在 `~/.tlon/{agent-id}/config/`：

```
~/.tlon/
└── lin-zhiyao/
    └── config/
        ├── agent.yaml       # 基础配置（本节）
        ├── behavior.tlon    # 行为定义（MemberSpec，见下节）
        └── IDENTITY.md / SOUL.md / RULES.md   # 人设叙述（纯文本）
```

### 完整配置示例

`~/.tlon/lin-zhiyao/config/agent.yaml`：

```yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"

  # activation 已废弃：启动集合由 behavior.tlon 的 :on :boot 规则决定

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
| `id` | string | 是 | 唯一标识符，也是邮件地址的本地部分（`<id>@tlon.internal`） |
| `name` | string | 是 | 显示名称，用户可见 |
| `activation` | string | 否 | ⚠️ **已废弃**：写了会被静默忽略，不报错也不生效。启动集合由 `behavior.tlon` 的 `:on :boot` 规则决定（见下节） |
| `idle_timeout_seconds` | int | 否 | 空闲沉睡阈值（默认 600），仅对 lazy 成员生效 |
| `proactive` | bool | 否 | ⚠️ **已退役**：写了会被静默忽略，不报错也不生效。是否响应邮件由 `behavior.tlon` 的 trigger 决定（见下节） |

人设叙述（identity/profile/behavior 文本）不在 agent.yaml 里，
而是同目录下的 `IDENTITY.md` / `SOUL.md` / `RULES.md`。

#### LLM 配置

| 字段 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `model_name` | string | `kimi-k2.5` | 模型名称 |
| `temperature` | float | `1.0` | 创造性 (0=保守, 2=创意) |
| `max_tokens` | int | `4096` | 最大生成 token 数 |
| `top_p` | float | `0.9` | 核采样 |

#### Docker 配置

| 字段 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `image` | string | `ubuntu:22.04` | 容器镜像 |
| `memory_limit` | string | `512m` | 内存限制 |
| `cpu_limit` | float | `1.0` | CPU 限制 |
| `network_mode` | string | `host` | 网络模式 |
| `working_dir` | string | `/workspace` | 容器内工作目录 |

---

## 行为配置 (behavior.tlon)

Agent 的行为——何时响应邮件、认知循环上限、上下文组成——不在
agent.yaml，而在 `~/.tlon/{agent-id}/config/behavior.tlon`
（统一 MemberSpec，EDN 语法），一份规约解释执行即一个成员的运行。
规约是**全部**（total）：文件即行为的全部，缺失或解析失败拒绝该成员启动。

规约由**规则（rules）**组成：每条规则 = `:on`（订阅的事件）+ `:when`
（命中后的过滤，纯谓词）+ 可选 `:loop`（认知会话）+ `:actions`（动作）。
含 `:on :boot` 规则的成员开机即启动；无 `:loop` 的规则是确定性规则
（命中直接执行动作，零 LLM）。

```clojure
{:version 1
 :rules [{:name "respond"                    ; 认知规则：走 LLM 会话
          :on :mail-delivered
          :when (and (addressed-to-me? msg)  ; To 含我的地址
                     (content-matches? msg "？$"))  ; 且内容以问号结尾
          :loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3}
          :actions [(emit "本轮处理完毕" [])]}]}
```

可用谓词：`addressed-to-me?`（To 含我的地址）、`cc-ed?`（CC 含我的地址）、
`content-matches?`、`contains?`、`=`、`not`。
完整设计见 [dsl-agents.md](../../design/dsl-agents.md)。

---

## ~~Skill 配置~~

> ❌ **Skill 系统已于 2026-03-17 移除**
>
> 原 Skill 配置已失效。如需扩展 Agent 能力，请直接通过 `executeBashCommand` 执行命令。

---

## 运行时配置

### 服务端端口

```bash
uv run tlon dream \
  --host 127.0.0.1 \
  --rest-port 8080 \
  --ws-port 8888
```

### 客户端连接

```bash
uv run tlon ops \
  --username guest \
  --host 127.0.0.1 \
  --port 8888 \
  --rest-port 8080
```

---

## 数据存储

所有运行时数据在 `~/.tlon/`（可用 `TLON_HOME` 覆盖）。

### Agent 数据目录

```
~/.tlon/{agent-id}/
├── config/                 # agent.yaml / behavior.tlon / *.md
├── memory/
│   ├── working/
│   │   └── interactions.jsonl   # 工作记忆（交互记录）
│   └── episodic/
│       ├── summaries.jsonl      # 情节记忆（会话摘要）
│       └── facts.jsonl          # 事实库（结构化数据）
├── mail/
│   ├── inbox.jsonl         # 收件箱
│   └── sent.jsonl          # 发件箱
└── workspace/              # 工作区（挂载进容器）
```

人类用户的邮箱在 `~/.tlon/human-{username}/mail/`。

### 共享数据

```
~/.tlon/shared/
├── logs/server-{date}.jsonl     # 服务端日志（按天滚动）
├── traces/trace-{uuid}.jsonl    # 会话 trace
└── chat_history/
```

### 文件格式

记忆与邮箱文件均为 JSONL（每行一条 JSON 记录）。
记忆格式详情见 [memory.md](../../design/memory.md)。

---

## 配置验证

```bash
# 使用向导验证/修改
uv run tlon mirror

# 手动检查 (Python)
uv run python -c "from tlon.base.settings import get_settings; print(get_settings().llm_model_name)"
```
