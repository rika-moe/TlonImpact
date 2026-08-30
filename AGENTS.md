# Tlon 开发指南


## 项目规范

1. 不保留向后兼容。过时的直接删，别加兼容层、别写migration、别留fallback。
2. 选能满足当前需求的最简单实现。不要预防性抽象，不要多此一举的配置层。
3. 系统分层长。先跑通一个最小的端到端版本，再往上加东西。绝不为了未完成的复杂度拆掉能跑的东西。
4. 组件保持模块化，关注点分离。
5. 优先用成熟的、有人维护的库。没有明确理由别自己重写。
6. 先翻项目里已有的依赖能做什么，再考虑加新包或自己写。别上来就假设库里没有。
7. 架构决策往长了做。不接受"先这样以后再换"的临时方案。
8. 先看成熟产品怎么解决同一个问题，用已验证的模式，别从零发明。

## 快速开始

```bash
# 安装依赖
uv sync

# 配置环境（Tlon 读 ~/.tlon/.env，.env.example 是模板）
cp .env.example ~/.tlon/.env
# 编辑 ~/.tlon/.env，填入 LLM_API_KEY（可选改 LLM_MODEL=provider/model）

# 运行（服务端 + 客户端）
uv run tlon dream     # 终端 1（服务端，REST + SSE，默认 127.0.0.1:8080；启动 banner 显示 WebUI 地址）
uv run tlon ops       # 终端 2（Operator TUI，含 mail 视图——人类邮件主界面）
# 浏览器 WebUI 由 dream 托管于 /ui/（需先 cd apps/webui && npm install && npm run build）

# Telegram 客户端（可选第三条人类通道，手机收发邮件）
uv sync --extra telegram
# ~/.tlon/.env 配置 TELEGRAM_BOT_TOKEN=<BotFather token>、TELEGRAM_ALLOWED_USERS=<user_id 逗号分隔>
uv run tlon telegram  # 手机 /send <名字> <内容> 发信；对来信推送点「回复」回信；/inbox 看未读

# 单元测试
uv run pytest
```

-- 
## 开发规范
- **禁止直接推送 main 分支** — 所有修改必须通过分支 + PR
- 合理划分模块，高内聚，低耦合
- 排查bug要深挖根因，纵览全局，防止后续出现相同bug
- **必须使用 type hint 类型注解**
- 必须使用lint工具: `uv run ruff`

---

## 协作工作流（人与 AI 结对）

### 流程
- 一切改动走分支 + PR；PR 合并（squash）后先 `checkout main && pull` 再拉新分支
- git 操作（建分支 / commit / push / 创建 PR）等用户明确指令，不擅自执行
- 方向性工作先讨论对齐、用户拍板后才写代码；拍板的决策先写进 docs 再实施
- 大任务拆阶段，每阶段独立提交或独立 PR；行为不变先行，能力扩张随后

### 执行
- 大活派 subagent：brief 给精确的文件:行号、已核实的现状事实、已定稿决策、测试要求、验证命令；超时/中断用 resume 续跑；完成后必须自己复核（八件套 + 审 diff + 真实冒烟），不盲信汇报
- 验证八件套：`uv run pytest src/tests/ -q`、`uv run ruff check src/`、`uv run python scripts/check_architecture.py`、`uv run python scripts/check_no_reexport.py`、`uv run python scripts/check_stage_purity.py`、`uv run mypy src/tlon`、`uv run lint-imports`、`uv run python scripts/gen_reference_docs.py --check`（文档保鲜门），全绿才算完
- 真实冒烟：起 dream + SDK 发邮件 + 查 `~/.tlon` 收件箱验证；完事清理冒烟痕迹、释放 8080 端口

### 质量准则
- 既有测试无需保障通过率， 没通过的单元测试可以直接删除明
- 文档与代码同步：设计文档随代码更新；历史达成情况保留，被废止的决策用删除线或新决策覆盖
- 诚实优先：review 给证据说话；发现用户指令有问题时摆数据提出，由用户定夺

### 沟通
- 汇报带实质：改动清单、测试数、六件套结果、与计划的偏差及原因
- 未完成/未验证的不说成完成；过程事故（subagent 超时、替换误伤等）如实记录

---

*最后更新：2026-08-30*
