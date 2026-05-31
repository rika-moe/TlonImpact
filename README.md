# Tlon — 你的影子团队

> **一个运行在终端里的多 Agent 协作系统。**
> 你说一个需求，一队 AI Agent 帮你规划、编码、交付。

```bash
uv sync && uv run tlon
```

---

## 为什么不用 ChatGPT？

| | Tlon | ChatGPT | Cursor |
|---|---|---|---|
| **多角色协作** | ✅ PM + 开发 + 运维同步工作 | 单轮对话 | 单人辅助 |
| **持久记忆** | ✅ 记得你三个月前的决策 | 会话即忘 | 文件级 |
| **代码执行** | ✅ Docker 沙箱隔离 | 不能执行 | 编辑器内 |
| **Git 集成** | ✅ 分支即平行宇宙 | 无 | 原生 |
| **终端原生** | ✅ TUI + REST API | 网页 | IDE |

---

## 你需要什么

- Python 3.11+
- Docker（沙箱执行环境）
- OpenAI API Key（或兼容的 LLM 服务）

---

## 快速开始

```bash
uv sync
uv run tlon mirror       # 配置你的第一个团队
uv run tlon dream        # 启动服务
uv run tlon intrude      # 打开聊天界面
```

命令是文学命名的。`dream` = `server`，`intrude` = `tui`。

```
> @林知遥 我要做一个个人博客

[林知遥] 协调中... @阿杰 分析需求，@陈代码 评估技术栈
[阿杰]   核心功能：文章管理、标签、评论。建议 Next.js + PostgreSQL
[陈代码] 可行，我来初始化项目。 [执行：mkdir blog && npm init]
```

---

## 文档入口

| 你想... | 去这里 |
|---|---|
| **理解概念** | [用户指南](docs/user/README.md) |
| **纯命令清单** | [快速开始](docs/user/engineering/quickstart.md) |
| **配置 Agent** | [配置说明](docs/user/engineering/configuration.md) |
| **自己造轮子** | [架构文档](docs/architecture/README.md) |
| **路线图** | [里程碑](docs/roadmap/README.md) |

---

## 许可证

基于 Python、Pydantic、Textual 构建。
