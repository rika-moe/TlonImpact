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
curl http://localhost:8080/api/health  # 服务端响应?

# 3. 日志检查
tail -f data/logs/server.log
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
Error: Cannot connect to server at 127.0.0.1:8888
```

**原因**: 服务端未启动或端口错误

**解决**:
```bash
# 确认服务端运行
ps aux | grep "minions server"

# 检查端口监听
lsof -i :8080  # REST API
lsof -i :8888  # WebSocket

# 重新启动服务端
uv run tlon dream
```

---

#### 现象: `Permission denied`

```
Error: EACCES: permission denied, mkdir './data'
```

**原因**: 当前目录无写入权限

**解决**:
```bash
# 检查权限
ls -la .

# 修复权限
chmod 755 .
mkdir -p data

# 或使用其他目录
export DATA_DIR=/path/with/permission
```

---

### 2. Agent 问题

#### 现象: Agent 无响应

```
> @陈代码 写代码
[系统] 正在唤醒 陈代码...
(无后续响应)
```

**诊断步骤**:

```bash
# 1. 检查 Agent 是否存在
curl http://localhost:8080/api/agents

# 2. 检查 Agent 状态
curl http://localhost:8080/api/agents/dev-001

# 3. 检查 Docker 容器
docker ps -a | grep dev-001
docker logs dev-001-container

# 4. 检查服务端日志
grep "dev-001" data/logs/server.log
```

**可能原因与解决**:

| 原因 | 现象 | 解决 |
|-----|------|------|
| 容器启动失败 | `docker ps` 无容器 | 检查 Docker 资源，重启服务端 |
| LLM API 错误 | 日志显示 API 调用失败 | 检查 API Key 有效性 |
| 配置错误 | 日志显示配置解析错误 | 检查 `config/agents/*.yaml` 语法 |

---

#### 现象: Agent 重复响应/循环

```
[陈代码] @林知遥 请确认
[林知遥] @陈代码 已确认
[陈代码] @林知遥 好的
[林知遥] @陈代码 收到
...
```

**原因**: Agent 间无限互相 @

**解决**:
```bash
# 1. 在 TUI 中手动休眠一方
/sleep 陈代码

# 2. 重启服务端 (紧急)
Ctrl+C
uv run tlon dream

# 3. 修改配置，添加规则
# config/agents/{id}.yaml:
rules:
  - "不主动@其他Agent，除非被明确要求"
```

---

#### 现象: 记忆丢失

```
> 继续昨天那个项目
[陈代码] 什么项目？我没有相关记忆。
```

**诊断**:
```bash
# 检查记忆文件
ls -la data/agents/dev-001/memory/
cat data/agents/dev-001/memory/episodes.json
cat data/agents/dev-001/memory/facts.json
```

**可能原因**:

| 原因 | 检查 | 解决 |
|-----|------|------|
| 文件被删除 | 文件不存在 | 无法恢复，重新积累 |
| 摘要未生成 | 只有 working.json | 手动触发 `/summary` |
| 路径错误 | `DATA_DIR` 被修改 | 检查环境变量 |

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

```
[系统] 正在唤醒 陈代码...
[错误] Docker container start timeout
```

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
# config/agents/{id}.yaml:
docker:
  image: "alpine:latest"
```

---

#### 现象: 容器内存溢出

```
[陈代码] [执行: npm build]
[错误] Container OOM killed
```

**解决**:
```bash
# 增加内存限制
# config/agents/{id}.yaml:
docker:
  memory_limit: "2g"  # 从 512m 增加

# 重启 Agent
/sleep 陈代码
/wake 陈代码
```

---

### 4. LLM 问题

#### 现象: `Rate limit exceeded`

```
Error: RateLimitError: You exceeded your current quota
```

**解决**:
```bash
# 检查配额
# 登录 OpenAI 控制台查看

# 降低调用频率
# 修改 temperature 减少重试
# config/agents/{id}.yaml:
llm:
  temperature: 0.3  # 更确定性，减少重试

# 或使用其他模型
llm:
  model_name: "gpt-3.5-turbo"  # 更便宜
```

---

#### 现象: 响应质量下降

```
[陈代码] 胡言乱语，代码错误
```

**诊断**:
```bash
# 检查上下文长度
wc -c data/agents/dev-001/memory/working.json
```

**解决**:
```bash
# 1. 清空工作记忆
/summary  # 触发归档

# 2. 更换模型
llm:
  model_name: "gpt-4-turbo"  # 更强模型
```

---

### 5. TUI 问题

#### 现象: 界面乱码

```
[??] ???
```

**解决**:
```bash
# 检查终端编码
echo $LANG  # 应为 utf-8

# 设置编码
export LANG=en_US.UTF-8

# 使用支持 Unicode 的终端
# iTerm2, Windows Terminal, GNOME Terminal
```

---

#### 现象: 无法复制粘贴

**解决**:
```bash
# 使用简单 TUI (默认)
uv run tlon intrude

# 或使用 REST API 发送消息
curl -X POST http://localhost:8080/api/chatroom/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "@陈代码 写代码"}'
```

---

## 调试模式

### 启用调试日志

```bash
# 服务端调试
uv run tlon dream --debug

# 或环境变量
export LOG_LEVEL=DEBUG
uv run tlon dream
```

### 日志位置

```
data/logs/
├── server.log      # 服务端日志
├── agents/         # Agent 日志
│   ├── dev-001.log
│   └── support-001.log
└lineas/websocket.log  # WebSocket 日志
```

### 查看实时日志

```bash
# 服务端
tail -f data/logs/server.log

# 特定 Agent
tail -f data/logs/agents/dev-001.log

# 所有日志
find data/logs -name "*.log" -exec tail -f {} +
```

---

## 数据备份与恢复

### 备份

```bash
# 备份所有数据
tar czvf tlon-backup-$(date +%Y%m%d).tar.gz data/

# 备份特定 Agent
tar czvf agent-backup.tar.gz data/agents/dev-001/

# 备份配置
tar czvf config-backup.tar.gz config/
```

### 恢复

```bash
# 恢复数据
tar xzvf tlon-backup-20240309.tar.gz

# 注意: 恢复前停止服务端
pkill -f "minions server"

# 恢复后重启
uv run tlon dream
```

---

## 重置系统

### 软重置 (保留配置)

```bash
# 停止服务端
pkill -f "minions server"

# 清空数据
rm -rf data/agents/*/memory/*

# 重启
uv run tlon dream
```

### 硬重置 (清空所有)

```bash
# 停止服务端
pkill -f "minions server"

# 清空所有数据
rm -rf data/

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
ls config/agents/

# 状态检查
curl http://localhost:8080/api/health
curl http://localhost:8080/api/status
```

### 报告问题

报告问题时提供:

1. **版本信息**: `minions --version`
2. **错误日志**: `data/logs/server.log` 最后 50 行
3. **复现步骤**: 从启动到出错的具体操作
4. **环境信息**: OS, Python 版本, Docker 版本
