# Tlon 工程文档

> 纯技术参考，无叙事。适合快速查阅和故障排查。

---

## 文档索引

| 文档 | 内容 | 阅读时机 |
|-----|------|---------|
| [quickstart.md](quickstart.md) | 安装、配置、启动命令清单 | 首次使用 |
| [configuration.md](configuration.md) | 环境变量、配置文件、Agent 定义 | 自定义配置 |
| [commands.md](commands.md) | CLI 命令、TUI 快捷键、API 端点 | 日常查阅 |
| [troubleshooting.md](troubleshooting.md) | 错误码、常见问题、调试方法 | 遇到问题 |
| [architecture.md](architecture.md) | 系统架构、数据流、组件关系 | 深度理解 |

---

## 30秒启动

```bash
# 1. 安装
pip install tlon

# 2. 配置 API Key
echo "OPENAI_API_KEY=sk-your-key" > .env

# 3. 启动服务端
tlon server

# 4. 新终端启动 TUI
tlon tui
```

---

## 关键路径速查

```
数据目录: ./data/
├── agents/{id}/          # Agent 数据
│   ├── memory/           # 记忆文件
│   │   ├── working.json  # 工作记忆
│   │   ├── episodes.json # 情节记忆
│   │   └── facts.json    # 事实库
│   └── state.json        # 运行状态
├── chatroom/             # 消息历史
└── logs/                 # 系统日志

配置目录: ./config/
└── agents.yaml           # Agent 定义

~~Skill 目录: ./skills/~~ ❌ 已移除（2026-03-17）
```

---

## 术语对照表

| 文学术语 | 工程术语 | 说明 |
|---------|---------|------|
| 代表大会 | Chatroom | WebSocket 消息总线 |
| 莎士比亚 | Memory System | 双层记忆：Working + Episodic |
| ~~巴别图书馆~~ | ~~Skill System~~ | ~~已移除~~ |
| 花园 | Git Semantic Layer | Git 操作的抽象层 |
| 镜子 | Mirror CLI | Agent 生命周期管理 |
| 环形废墟 | Meta-programming | Agent 自举能力 |

---

## 故障速查

| 现象 | 可能原因 | 解决 |
|-----|---------|------|
| `OPENAI_API_KEY not set` | 未配置 API Key | 创建 `.env` 文件 |
| Connection refused | 服务端未启动 | 先运行 `minions server` |
| Agent 无响应 | 容器未启动 | 检查 Docker 状态 |
| 记忆丢失 | 数据目录被删除 | 备份 `data/agents/` |

完整故障排查见 [troubleshooting.md](troubleshooting.md)。

---

## 环境变量全表

```bash
# 必需
OPENAI_API_KEY=sk-...           # LLM API Key

# 可选
OPENAI_BASE_URL=https://...     # 自定义 API 端点
OPENAI_MODEL_NAME=gpt-4         # 默认模型
OPENAI_TEMPERATURE=0.7          # 生成温度
DATA_DIR=./data                 # 数据目录
LOG_LEVEL=INFO                  # 日志级别
DOCKER_DEFAULT_IMAGE=ubuntu:22.04
DOCKER_NETWORK_MODE=host
```

---

**注意**: 本文档集假设读者已了解多 Agent 系统的基本概念。如需概念性解释，请阅读上层文学型文档。