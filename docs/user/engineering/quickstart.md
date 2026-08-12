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

### 2. 创建 Agent 团队

```bash
uv run tlon mirror
```

向导会检查环境、验证 API 连接，并创建默认的 6 个 Agent
（配置写入 `~/.tlon/{agent-id}/config/`）。

---

## 启动

终端 1 - 启动服务端（HTTP :8080，REST + SSE）:
```bash
uv run tlon dream
```

终端 2 - 启动 Operator 控制台:
```bash
uv run tlon ops --username 你的名字
```

`--username` 设定你的邮件身份（缺省 `guest`，也可用环境变量 `TLON_USERNAME`），
你的邮箱地址是 `<username>@tlon.internal`。

> 别名：`dream` = `server`；`ops` 的旧别名是 `intrude` / `tui`。

### 自定义端口

```bash
# 服务端
uv run tlon dream --rest-port 8080

# 客户端
uv run tlon ops --username 你的名字 --rest-port 8080
```

---

## 验证运行

Operator 控制台默认打开 mail 视图。写第一封邮件：

1. 按 `c` 进入写信表单（四个字段：To / Cc / Subject / Content）
2. `To` 填 `gu-yanxi@tlon.internal`（客服顾言蹊），回车进入下一字段
3. 填 Subject 和 Content，在 Content 字段回车提交（`esc` 取消）

发出后，顾言蹊会读取未读邮件并回信；回信会出现在你的邮箱里
（mail 视图中用 `↑`/`↓` 浏览，按 `r` 回复）。

也可以用 REST 确认邮件已送达：

```bash
curl "http://localhost:8080/inbox?username=guest&limit=10"
```

---

## 常用操作速查

### Operator 按键

```
1-5 / tab / ←→   切换视图 (mail · agents · events · traces · config)
q / Ctrl+C       退出

# mail 视图
↑ / ↓            选择邮件
c                写信 (To/Cc/Subject/Content，回车逐字段，Content 回车提交)
r                回复选中邮件 (自动带上收件人与线程)
esc              取消表单
```

### 邮件地址

```
<agent-id>@tlon.internal     某个 Agent，如 gu-yanxi@tlon.internal
<username>@tlon.internal     某个人类用户
all@tlon.internal            全员邮件列表
```

发信必须显式指定收件人；地址写错会收到 `postmaster@tlon.internal` 的退信。
Agent 沉睡时邮件留存在邮箱里，醒来后会扫未读逐封处理。

### 服务端管理

```bash
# 查看日志 (JSONL，按天滚动)
tail -f ~/.tlon/shared/logs/server-$(date +%F).jsonl

# 健康检查
curl http://localhost:8080/health

# 成员列表 (含各成员邮件地址)
curl http://localhost:8080/participants

# Agent 列表与状态
curl http://localhost:8080/agents

# 某个用户的邮箱 (收件+发件)
curl "http://localhost:8080/inbox?username=guest&limit=20"
```

---

## 目录结构

运行时数据全部在家目录 `~/.tlon/` 下（可用 `TLON_HOME` 覆盖）：

```
~/.tlon/
├── shared/
│   ├── logs/
│   │   └── server-{date}.jsonl      # 服务端日志
│   ├── traces/
│   │   └── trace-{uuid}.jsonl       # 会话 trace
│   └── chat_history/
├── {agent-id}/                      # 每个 Agent 一个家目录
│   ├── config/
│   │   ├── agent.yaml               # id/name/llm/docker 等基础配置
│   │   ├── behavior.tlon            # 行为定义 (MemberSpec, EDN 语法)
│   │   └── IDENTITY.md / SOUL.md / RULES.md
│   ├── memory/
│   │   ├── working/interactions.jsonl
│   │   └── episodic/summaries.jsonl, facts.jsonl
│   ├── mail/
│   │   ├── inbox.jsonl
│   │   └── sent.jsonl
│   └── workspace/
└── human-{username}/
    └── mail/                        # 人类用户邮箱
```

---

## 下一步

- 配置详情: [configuration.md](configuration.md)
- 命令参考: [commands.md](commands.md)
- 故障排查: [troubleshooting.md](troubleshooting.md)
