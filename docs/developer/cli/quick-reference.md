# Tlon CLI 速查表

## 常用命令

```bash
# 启动服务（先执行）
tlon dream

# 配置 Agent
tlon mirror

# 进入 TUI（再开一个终端）
tlon intrude
# 或
tlon intrude tui
```

## 完整命令表

| 命令 | 别名 | 功能 |
|-----|------|------|
| `tlon dream` | | 启动 Server |
| `tlon dream --lucid` | `-l` | Debug 模式 |
| `tlon mirror` | | 交互式配置 |
| `tlon mirror --reflect` | `-r` | 查看 Agents |
| `tlon intrude` | | 默认 TUI |
| `tlon intrude tui` | | 终端界面 |
| `tlon intrude web` | | Web 界面 |
| `tlon intrude telegram` | | Telegram |
| `tlon intrude void` | | 静默模式 |
| `tlon --version` | `-v` | 版本 |
| `tlon --help` | `-h` | 帮助 |

## 文学对应

```
dream    → 《环形废墟》→ Server
mirror   → 《镜子与面具》→ 配置/创造
intrude  → 入侵现实 → 连接界面
```

## 典型工作流

```bash
# 终端 1：启动梦境
tlon dream

# 终端 2：入侵现实
tlon intrude

# 终端 3（可选）：镜中造物
tlon mirror
```
