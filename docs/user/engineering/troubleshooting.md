# 故障排查

> 所有命令需在项目目录下通过 `uv run tlon` 执行。以下用 `tlon` 指代，实际输入时加 `uv run` 前缀。

> 问题诊断与解决方案。

---

## 快速诊断

### 检查清单

遇到问题时，按顺序检查:

```bash
# 1. 环境检查
python --version        # >= 3.11?
echo $OPENAI_API_KEY    # 已设置?
docker ps               # Docker 运行中?

# 2. 服务端检查
curl http://localhost:8080/health  # 服务端响应?

# 3. 日志检查（structlog JSONL，按日期分文件）
tail -f ~/.tlon/shared/logs/server-*.jsonl
```

---

## 常见问题

### 1. 启动失败

#### 现象: `OPENAI_API_KEY not set`

```
==================================================
  Configuration Error
==================================================
  ❌ OPENAI_API_KEY not set in .env file
==================================================
```

**原因**: 未配置 LLM API Key

**解决**:
```bash
# 方法 1: 环境变量
export OPENAI_API_KEY=sk-your-key

# 方法 2: .env 文件
echo "OPENAI_API_KEY=sk-your-key" > .env
```

---

#### 现象: `Connection refused`

```
Error: Cannot connect to server at 127.0.0.1:8080
```

**原因**: 服务端未启动或端口错误

**解决**:
```bash
# 确认服务端运行
ps aux | grep "tlon dream"

# 检查端口监听
lsof -i :8080  # HTTP API (REST + SSE)

# 重新启动服务端
uv run tlon dream
```

---

#### 现象: 家目录无写入权限

**原因**: `~/.tlon/`（或 `TLON_HOME` 指向的目录）无写入权限

**解决**:
```bash
# 检查权限
ls -ld ~/.tlon

# 修复权限
chmod 755 ~/.tlon

# 或改用其他目录
export TLON_HOME=/path/with/permission
```

---

### 2. Agent 问题

#### 现象: Agent 无响应

```
> 写信给 林知遥：写代码
（无后续响应）
```

邮件模型下，按这个顺序分诊：

**1. 地址写对了吗？**

收件人必须是完整地址 `<agent-id>@tlon.internal`。用 `/participants` 查当前成员列表：

```bash
curl http://localhost:8080/participants
```

**2. 收到 postmaster 退信了吗？**

地址不存在时，`postmaster@tlon.internal` 会把退信投进**你的**邮箱。查自己的收件箱：

```bash
curl "http://localhost:8080/inbox?username=<你的用户名>"
```

**3. 对方在沉睡吗？**

lazy Agent（behavior.tlon 无 `:on :boot` 规则）被来信唤醒需要数秒。查状态（`sleeping` / `waking` / `active`）：

```bash
curl http://localhost:8080/agents
curl http://localhost:8080/agents/lin-zhiyao
```

**4. 查服务端日志**

```bash
grep "agent_waking" ~/.tlon/shared/logs/server-*.jsonl
grep "mail" ~/.tlon/shared/logs/server-*.jsonl
```

**可能原因与解决**:

| 原因 | 现象 | 解决 |
|-----|------|------|
| 地址拼写错误 | 收到 postmaster 退信 | 用 `/participants` 核对 id 后重写 |
| Agent 沉睡中 | 状态 `sleeping`/`waking` | 等待数秒，唤醒后自动处理 |
| LLM API 错误 | 日志显示 API 调用失败 | 检查 API Key 有效性 |
| 配置错误 | 日志显示配置解析错误 | 检查 `~/.tlon/{id}/config/agent.yaml` 语法 |

---

#### 现象: Agent 重复响应/循环

```
[林知遥] 回信给 沈清晏：请确认
[沈清晏] 回信给 林知遥：已确认
[林知遥] 回信给 沈清晏：好的
...
```

**原因**: 两个 Agent 互相把对方列为收件人，每封回信都触发对方响应，形成邮件循环。

**解决**:

```bash
# 1. 紧急止血：重启服务端
Ctrl+C
uv run tlon dream
```

然后治本——循环的根源是触发条件太宽。检查并收紧相关 Agent 的触发规约：

```clojure
; ~/.tlon/{id}/config/behavior.tlon
; 只在自己是主要收件人（To）且内容匹配时才响应
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (and (addressed-to-me? msg)
                     (not (content-matches? msg "^自动回复")))}]}
```

同时检查 `~/.tlon/{id}/config/RULES.md`，可以加入约束，例如「收到 Agent 的确认类邮件后不再回信」「完成任务汇报后结束线程」。

> 提示：触发规约是数据，不是命令——没有 `/sleep` 之类的运行时开关。调整 `behavior.tlon` 或 RULES.md 后，重启服务端生效。

---

#### 现象: 记忆丢失

```
> 写信给 林知遥：继续昨天那个项目
[林知遥] 什么项目？我没有相关记忆。
```

**诊断**:
```bash
# 检查记忆文件
ls -la ~/.tlon/lin-zhiyao/memory/
cat ~/.tlon/lin-zhiyao/memory/episodic/summaries.jsonl
cat ~/.tlon/lin-zhiyao/memory/episodic/facts.jsonl
cat ~/.tlon/lin-zhiyao/memory/working/interactions.jsonl
```

**可能原因**:

| 原因 | 检查 | 解决 |
|-----|------|------|
| 文件被删除 | 文件不存在 | 无法恢复，重新积累 |
| 摘要未生成 | 只有 working/interactions.jsonl | 写信要求 Agent 归档（它会调用 `compact` 工具） |
| 路径错误 | `TLON_HOME` 被修改 | 检查环境变量 |

> 注意：`role=contractor` 的一次性 Agent 被回收时家目录（含记忆）会被删除，这是设计行为。

---

### 3. Docker 问题

#### 现象: `Cannot connect to Docker daemon`

```
Error: DockerException: Error while fetching server API version
```

**解决**:
```bash
# 检查 Docker 服务
sudo systemctl status docker

# 启动 Docker
sudo systemctl start docker

# 或使用 Docker Desktop (macOS/Windows)
open -a Docker

# 检查权限 (Linux)
sudo usermod -aG docker $USER
# 重新登录生效
```

---

#### 现象: 容器启动超时

**原因**: Docker 资源不足或镜像拉取慢

**解决**:
```bash
# 检查资源
docker system df

# 清理资源
docker system prune

# 预拉取镜像
docker pull ubuntu:22.04

# 或修改配置使用轻量镜像
# ~/.tlon/{agent-id}/config/agent.yaml:
# agent:
#   docker:
#     image: "alpine:latest"
```

---

#### 现象: 容器内存溢出

```
[错误] Container OOM killed
```

**解决**:

提高内存限制：

```yaml
# ~/.tlon/{agent-id}/config/agent.yaml
agent:
  docker:
    memory_limit: "2g"  # 默认 512m
```

然后重启服务端使配置生效（lazy agent 会在下次唤醒时使用新配置）。

---

### 4. LLM 问题

#### 现象: `Rate limit exceeded`

```
Error: RateLimitError: You exceeded your current quota
```

**解决**:

```yaml
# 登录 LLM 提供商控制台检查配额，然后调整 ~/.tlon/{agent-id}/config/agent.yaml
agent:
  llm:
    temperature: 0.3            # 更确定性，减少重试
    model_name: "更便宜的模型"    # 降低成本
```

---

#### 现象: 响应质量下降

```
[林知遥] 胡言乱语，代码错误
```

**诊断**:
```bash
# 检查工作记忆大小
wc -c ~/.tlon/lin-zhiyao/memory/working/interactions.jsonl
```

**解决**:
```bash
# 1. 写信要求 Agent 归档当前会话（触发 compact 工具）

# 2. 或更换更强的模型
# ~/.tlon/{agent-id}/config/agent.yaml:
# agent:
#   llm:
#     model_name: "更强的模型"
```

---

### 5. Operator 控制台问题

Operator 是基于 Ink（React for CLI）的 TypeScript TUI，对终端环境有要求。

#### 现象: 界面乱码

**解决**:
```bash
# 检查终端编码
echo $LANG  # 应为 UTF-8

# 设置编码
export LANG=en_US.UTF-8

# 使用支持 Unicode 的现代终端
# iTerm2, Windows Terminal, GNOME Terminal
```

#### 现象: 界面渲染异常（错位、闪烁）

**解决**:
```bash
# Ink 依赖 ANSI 转义序列，确认 TERM 设置正确
echo $TERM   # 应为 xterm-256color 或类似

# 把终端窗口拉宽后重启控制台
uv run tlon ops
```

#### 现象: 控制台不可用时的兜底

读邮件不需要 TUI——直接查 REST API：

```bash
# 查自己的收件箱（inbox + sent 合并，按时间序）
curl "http://localhost:8080/inbox?username=<你的用户名>&limit=50"

# 查成员与状态
curl http://localhost:8080/participants
curl http://localhost:8080/agents
```

发邮件走 `POST /messages`（同步回执，202 + `mail_id`），Operator 客户端与 SDK 均走该端点。

---

## 调试模式

### 启用调试日志

```bash
# 服务端调试（lucid = 清醒梦模式）
uv run tlon dream --lucid

# 或全局 debug 开关
uv run tlon --debug dream
```

### 日志与 Trace

```
~/.tlon/shared/
├── logs/
│   └── server-{date}.jsonl   # 服务端日志（structlog JSONL，按日期分文件）
└── traces/                   # Trace 数据（每次请求/会话的调用链）
```

也可以通过 REST API 查询：

```bash
curl http://localhost:8080/logs                    # 日志
curl http://localhost:8080/agents/lin-zhiyao/logs  # 特定 Agent 日志
curl http://localhost:8080/traces                  # Trace 列表
```

### 查看实时日志

```bash
# 服务端
tail -f ~/.tlon/shared/logs/server-*.jsonl

# 过滤特定 Agent
grep "lin-zhiyao" ~/.tlon/shared/logs/server-*.jsonl
```

---

## 数据备份与恢复

### 备份

```bash
# 备份所有运行时数据（配置、记忆、邮箱、任务）
tar czvf tlon-backup-$(date +%Y%m%d).tar.gz ~/.tlon/

# 备份特定 Agent
tar czvf agent-backup.tar.gz ~/.tlon/lin-zhiyao/
```

### 恢复

```bash
# 恢复前停止服务端
pkill -f "tlon dream"

# 恢复数据
tar xzvf tlon-backup-20260730.tar.gz -C ~

# 恢复后重启
uv run tlon dream
```

---

## 重置系统

### 软重置 (保留配置)

```bash
# 停止服务端
pkill -f "tlon dream"

# 清空所有 Agent 的记忆（配置、邮箱保留）
rm -rf ~/.tlon/*/memory/*

# 重启
uv run tlon dream
```

### 硬重置 (清空所有)

```bash
# 停止服务端
pkill -f "tlon dream"

# 清空所有运行时数据（配置、记忆、邮箱、日志全部删除）
rm -rf ~/.tlon/

# 重新运行向导
uv run tlon mirror
uv run tlon dream
```

---

## 获取帮助

### 自查命令

```bash
# 系统信息
uv run tlon --version
python --version
docker --version

# 配置检查
cat .env
ls ~/.tlon/

# 状态检查
curl http://localhost:8080/health
curl http://localhost:8080/agents
```

### 报告问题

报告问题时提供:

1. **版本信息**: `uv run tlon --version`
2. **错误日志**: `~/.tlon/shared/logs/server-*.jsonl` 最后 50 行
3. **复现步骤**: 从启动到出错的具体操作
4. **环境信息**: OS, Python 版本, Docker 版本
