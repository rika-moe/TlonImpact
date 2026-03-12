# Tlon CLI 命令设计规范

> 文学意象与工程实现的交汇点

---

## 设计原则

1. **动词优先**：每个命令都是一个动作（dream, mirror, intrude）
2. **三层隐喻**：对应特隆宇宙的三个核心概念
3. **可扩展**：intrude 支持任意界面类型作为参数
4. **一致性**：所有命令保持 `tlon <verb> [target]` 结构

---

## 命令体系

### 核心三元组

| 命令 | 文学意象 | 功能 | 参数 |
|-----|---------|------|------|
| `dream` | 《环形废墟》的梦中人 | 启动/维持服务 | `--lucid` (debug) |
| `mirror` | 《镜子与面具》 | 创造/配置 Agent | `--reflect` (查看) |
| `intrude` | 特隆入侵现实 | 连接界面 | `tui/web/telegram/void` |

---

## 详细规范

### 1. dream - 维持梦境

启动 Tlon Server，维持特隆宇宙的运行。

```bash
# 标准启动
tlon dream

# 清醒梦模式（debug 日志）
tlon dream --lucid
tlon dream -l
```

**输出示例：**
```
[梦境开始] 特隆正在入侵现实...
           Server: ws://localhost:8765
           代表大会: 等待连接
           Agents: 休眠中
```

**实现备注：**
- 启动 WebSocket Server
- 初始化 Event Bus
- 加载 Agent 配置（但不启动容器）
- 等待 intrude 连接

---

### 2. mirror - 镜中造物

创造、配置、管理 Agents。

```bash
# 交互式配置向导
tlon mirror

# 查看现有 Agent 镜像
tlon mirror --reflect
tlon mirror -r

# 直接创建（非交互式，脚本用）
tlon mirror --create <name> --position <pos> --template <tpl>

# 销毁 Agent
tlon mirror --destroy <name>

# 修改配置
tlon mirror --edit <name>
```

**输出示例（交互式）：**
```
你站在镜前，镜中的轮廓在等待。

名字：_ _ _ _ _ _ _ _
性格：_ _ _ _ _ _ _ _
专长：_ _ _ _ _ _ _ _

[镜子] 轮廓凝聚，她睁开眼睛。
       "我是小雅，前端工程师。"
       小雅已进入代表大会。
```

**实现备注：**
- 读写 `config/agents.yaml`
- 管理 Docker 容器生命周期
- 持久化/清除记忆数据

---

### 3. intrude - 入侵现实

从特隆宇宙进入用户的现实世界（界面）。

```bash
# 默认入侵 TUI（最常用）
tlon intrude

# 指定入侵目标
tlon intrude tui        # 终端用户界面
tlon intrude web        # Web 浏览器界面
tlon intrude telegram   # Telegram Bot
tlon intrude void       # 静默模式（仅事件监听，无UI）
```

**参数说明：**

| 参数 | 界面类型 | 用途 |
|-----|---------|------|
| `tui` | Terminal UI (Textual) | 开发者首选，功能最全 |
| `web` | Web Browser (React) | 图形界面，跨平台 |
| `telegram` | Telegram Bot | 移动端，远程访问 |
| `void` | Headless | 后台监控，仅接收通知 |

**输出示例（TUI）：**
```
正在突破现实边界...
连接至 ws://localhost:8765 ... 成功

你已进入 代表大会

客服、项目经理、开发、运维...
他们各自携带着记忆，等待你的召唤。

[小夏] 您好！我是客服小夏，随时待命。
> _
```

**实现备注：**
- 检查 dream 是否运行
- 建立 WebSocket 连接
- 根据参数启动对应界面
- 同步历史消息

---

## 辅助命令

### 版本与帮助

```bash
tlon --version
tlon -v

tlon --help
tlon -h
```

### 无参数默认行为

```bash
$ tlon

🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

Commands:
  dream       维持梦境（启动 Server）
  mirror      镜中造物（配置 Agent）
  intrude     入侵现实（连接界面）

Usage:
  tlon dream                    # 启动服务
  tlon mirror                   # 配置向导
  tlon intrude [tui/web/telegram/void]  # 连接界面

Run 'tlon --help' for more information.
```

---

## 文学词源

### dream
- **出处**：《环形废墟》
- **意象**：梦中人创造现实，梦境的持续维持
- **技术映射**：Server 进程的持续运行

### mirror  
- **出处**：《镜子与面具》
- **意象**：照镜子创造分身，增殖与复制
- **技术映射**：Agent 的创建与配置

### intrude
- **出处**：特隆宇宙的核心隐喻
- **意象**：观念宇宙入侵现实，主客体界限模糊
- **技术映射**：从 Server 连接用户界面

**intrude 的张力：**
- 通常 "intrude" 是不请自来（负面）
- 这里用户主动选择 intrude（反转）
- 体现特隆宇宙的侵略性——它有自己的意志，正在"入侵"用户的现实

---

## 扩展预留

### 未来可能的 intrude 目标

```bash
tlon intrude vscode       # VS Code 插件
tlon intrude slack        # Slack Bot
tlon intrude discord      # Discord Bot
tlon intrude wechat       # 微信
tlon intrude mobile       # 移动端 App
```

### 未来可能的子命令

```bash
# 记忆管理
tlon memory --export      # 导出莎士比亚的记忆
tlon memory --import      # 导入记忆
tlon memory --forget      # 遗忘特定事实

# 图书馆管理
tlon library --list       # 列出可借阅的技能
tlon library --add        # 添加新书

# 花园管理（Git）
tlon garden --status      # 查看所有分支
tlon garden --prune       # 修剪废弃分支

# 环形废墟（高级）
tlon ruins --enable       # 启用递归创造
tlon ruins --tree         # 查看递归树
tlon ruins --pause        # 暂停递归
```

---

## 实现检查清单

- [x] `dream` 命令启动 Server
- [x] `dream --lucid` 启用 debug 日志
- [x] `mirror` 交互式向导
- [x] `mirror --reflect` 列出 Agents
- [x] `mirror --create` 非交互式创建 Agent
- [x] `mirror --destroy` 删除 Agent
- [x] `mirror --edit` 编辑 Agent
- [x] `intrude` 默认 TUI
- [x] `intrude tui` 明确指定 TUI
- [x] `intrude web` 启动 Web 客户端（预留接口）
- [x] `intrude telegram` 启动 Telegram Bot（预留接口）
- [x] `intrude void` 静默连接（预留接口）
- [x] 无参数时显示文学化帮助
- [x] 所有命令支持 `--help`
- [x] 向后兼容（`server`/`tui`/`wizard` 作为别名）

---

*文档版本：1.1*
*设计日期：2026-03-09*
*更新日期：2026-03-10*
*状态：已完全实现*
