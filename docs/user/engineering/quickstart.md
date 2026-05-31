# 快速开始

> 纯命令清单，无叙事。复制粘贴即可运行。

---

## 前提条件

```bash
# 检查 Python 版本 (需 >= 3.11)
python --version

# 检查 Docker
docker --version
docker ps  # 确认守护进程运行中
```

---

## 安装

```bash
# 安装依赖
uv sync
```

---

## 配置

### 1. API Key (必需)

```bash
# 方法 1: 环境变量
export OPENAI_API_KEY=sk-your-key-here

# 方法 2: .env 文件 (推荐)
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY
```

### 2. 验证配置

```bash
uv run tlon mirror
```

---

## 启动

### 方式 1: 分离模式 (推荐)

终端 1 - 启动服务端:
```bash
uv run tlon dream
```

终端 2 - 启动 TUI 客户端:
```bash
uv run tlon intrude
```

> `dream` = `server`, `intrude` = `tui`。

### 方式 2: 自定义端口

```bash
# 服务端
uv run tlon dream --rest-port 8080 --ws-port 8888

# 客户端
uv run tlon intrude --port 8888 --rest-port 8080
```

---

## 验证运行

在 TUI 中输入:
```
> 你好
```

预期响应:
```
[客服] 您好！有什么可以帮您的吗？
```

---

## 首次创建 Agent

```bash
# 使用向导
uv run tlon mirror

# 或手动编辑配置文件
vim config/agents/dev-001.yaml
```

Agent 配置示例 (`config/agents/{agent_id}.yaml`):
```yaml
agent:
  id: "dev-001"
  name: "林知遥"

  personality: "技术导向，注重细节"
  respond_to_all: false
  llm:
    model_name: "gpt-4"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

重启服务端后生效:
```bash
# Ctrl+C 停止旧进程
uv run tlon dream
```

---

## 常用操作速查

### TUI 操作

```
# 发送消息
直接输入文字，回车

# @ 提及 Agent
> @林知遥 写个函数

# 命令
/clear     # 清空对话
/agents    # 查看 Agent 列表
/help      # 显示帮助
/quit      # 退出
```

### 服务端管理

```bash
# 查看日志
tail -f data/logs/server.log

# 查看 Agent 状态
curl http://localhost:8080/api/agents

# 查看聊天室消息
curl http://localhost:8080/api/chatroom/messages
```

---

## 目录结构

```
.
├── .env                  # 环境变量
├── config/
│   ├── agents/           # Agent 定义
│   │   └── {id}.yaml

│       └── {id}.yaml
├── data/                 # 运行时数据 (勿删)
│   ├── agents/
│   │   └── {id}/
│   │       ├── memory/
│   │       │   ├── working/
│   │       │   │   └── interactions.jsonl
│   │       │   └── episodic/
│   │       │       ├── summaries.jsonl
│   │       │       └── facts.jsonl
│   │       └── state.json
│   ├── chatroom/
│   └── logs/
~~└── skills/~~               ~~# Skill 目录~~ ❌ 已移除
```

---

## 下一步

- 配置详情: [configuration.md](configuration.md)
- 命令参考: [commands.md](commands.md)
- 故障排查: [troubleshooting.md](troubleshooting.md)