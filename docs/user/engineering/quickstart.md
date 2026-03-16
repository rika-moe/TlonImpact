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
# 从 PyPI 安装
pip install tlon

# 或从源码安装
git clone https://github.com/yourusername/tlon.git
cd tlon
pip install -e ".[tui]"
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
tlon wizard
```

---

## 启动

### 方式 1: 分离模式 (推荐)

终端 1 - 启动服务端:
```bash
tlon server
```

终端 2 - 启动 TUI 客户端:
```bash
tlon tui
```

### 方式 2: 自定义端口

```bash
# 服务端
tlon server --rest-port 8080 --ws-port 8888

# 客户端
tlon tui --port 8888 --rest-port 8080
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
tlon wizard

# 或手动编辑配置文件
vim config/agents/dev-001.yaml
```

Agent 配置示例 (`config/agents/{agent_id}.yaml`):
```yaml
agent:
  id: "dev-001"
  name: "陈代码"
  position_id: "developer"
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
tlon server
```

---

## 常用操作速查

### TUI 操作

```
# 发送消息
直接输入文字，回车

# @ 提及 Agent
> @陈代码 写个函数

# 切换模式
/mode chat      # 聊天模式
/mode event     # 事件监控
/mode agent     # Agent 管理

# Agent 管理
/wake <name>    # 唤醒 Agent
/sleep <name>   # 休眠 Agent
/history        # 查看记忆
/facts          # 查看事实
/help           # 显示帮助
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
│   └── positions/        # Position 定义
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
└── skills/               # Skill 目录
    └── {skill-name}/
        ├── SKILL.md
        └── scripts/
```

---

## 下一步

- 配置详情: [configuration.md](configuration.md)
- 命令参考: [commands.md](commands.md)
- 故障排查: [troubleshooting.md](troubleshooting.md)