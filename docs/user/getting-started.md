# 第一章：进入特隆

> 一个由观念构建的宇宙，正在等待你的唤醒。

---

## 你将经历什么

安装 Tlon 只需要几分钟。但接下来发生的事情，可能会改变你对"一个人开发"的理解。

你会获得一个六人团队：
- 一个永远在线的客服
- 一个帮你拆解需求的项目经理
- 一个把关方案的产品经理
- 一个随叫随到的开发工程师
- 一个默默维护基础设施的运维
- 一个数据敏感的数据分析师

他们住在你的电脑里。他们记得你的一切。他们只在被需要时出现。

---

## 安装

### 前提

- Python 3.11+
- Docker（用于隔离执行环境）
- OpenAI API Key 或兼容的 LLM 服务

### 安装依赖

```bash
uv sync
```

### 第一次唤醒

```bash
uv run tlon mirror
```

向导会引导你完成四步：
- 环境检查（Python、Docker）
- API 配置（Base URL、API Key、模型）
- 团队配置（标准团队 6 个 Agent，或自定义）
- 确认创建（写入 `~/.tlon/{agent-id}/config/` 和 `.env`）

完成后，你会看到：

```
[镜子] 轮廓已凝聚。
       你的第一支团队已就绪。
       
       客服：顾言蹊
       项目经理：沈清晏
       开发：林知遥
       运维：江予白
       产品：苏晚晴
       数据分析：谢云舒
       
       启动服务：tlon dream
       打开控制台：tlon ops
```

---

## 启动

两个终端：

```bash
# 终端 1：启动服务端（HTTP :8080，REST + SSE）
uv run tlon dream

# 终端 2：打开 Operator 控制台
uv run tlon ops --username 你的名字
```

> `dream` 等价于 `server`；`ops` 的旧别名是 `intrude` / `tui`。

`--username` 设定你的邮件身份（缺省 `guest`），你的地址是 `<username>@tlon.internal`。
控制台默认打开 mail 视图——这是你与团队之间的邮箱，左侧是线程列表，右侧是会话流。

> 裸 `uv run tlon` 只打印帮助；服务端和控制台必须分开启动。

---

## 验证安装

在 mail 视图按 `c` 写信：`To` 填 `gu-yanxi@tlon.internal`，
`Content` 里随便写一句（比如"你好"），回车提交。

如果一切正常，客服顾言蹊会读取未读邮件并回信——新邮件出现在你的邮箱里，
用 `↑`/`↓` 选中，按 `r` 即可回复。

> 邮件必须显式指定收件人。地址写错会收到 `postmaster@tlon.internal` 的退信。

---

## 接下来

- [第二章：第一次对话](first-conversation.md) - 学会与影子团队交流
- [第三章：代表大会](advanced/congress.md) - 理解协作的规则
- [第四章：记忆](memory.md) - 他们如何记住你

---

## 🔧 工程参考

需要纯命令清单？→ [快速开始](engineering/quickstart.md) · [配置说明](engineering/configuration.md) · [故障排查](engineering/troubleshooting.md)

---

> **提示**
> 
> 如果你现在就想试试，给项目经理写封信：
> 按 `c`，`To` 填 `shen-qingyan@tlon.internal`，
> `Content` 写"我想做一个 TODO 应用，帮我拆解一下需求"。
> 
> 看看会发生什么。
