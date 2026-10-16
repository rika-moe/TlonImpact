# Tlon 文档地图

语言：全中文；代码标识符、EDN 关键字、API 字段名保持原文。

## 分层原则

| 层 | 回答 | 变化频率 | 更新触发 |
|---|---|---|---|
| [vision.md](./vision.md) | 为什么做 | 慢 | 方向讨论拍板后 |
| [architecture.md](./architecture.md) | 系统骨架是什么 | 慢 | 分层/装配/通信结构变化 |
| [design/](./design) | 各域为什么这样设计 | 中 | 对应域的设计决策变化 |
| [reference/](./reference) | 怎么写/怎么配/怎么调 | 快 | 代码变化时**必须同步** |
| [guides/](./guides) | 怎么做某件事 | 中 | 对应流程变化 |
| [decisions/](./decisions) | 当初为什么这样定（ADR） | 只增不改 | 新决策拍板；废止用删除线或新 ADR 覆盖 |

规则：

- 引用代码时写**文件路径 + 符号名**（如 [agents/member_spec.py](../src/tlon/agents/member_spec.py) 的 `MemberSpec`），不写行号——行号会随修改漂移。
- reference 类文档未来会进 prompt，按精确、自包含的标准写。
- 不写未实现的规划进 reference；规划只放 [vision.md](./vision.md) 和对应 design 文档的"方向"小节。
- 文档与代码同步是验收标准的一部分（见根目录 AGENTS.md）。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [vision.md](./vision.md) | 愿景、哲学、路线 | ✅ |
| [architecture.md](./architecture.md) | 分层、依赖方向、装配、运行时数据流、存储布局 | ✅ |
| [design/dsl.md](./design/dsl.md) | DSL 设计哲学：为何 EDN、为何受限、机制/策略分离 | ✅ |
| [design/runtime.md](./design/runtime.md) | MemberRuntime、认知循环、stage、effects-as-data | ✅ |
| [design/mail.md](./design/mail.md) | Republic of Letters：投递、线程、唤醒、人类接入 | ✅ |
| [design/memory.md](./design/memory.md) | 双层记忆与 Summarizer | ✅ |
| [design/prototypes.md](./design/prototypes.md) | 原型库、spawn、信任分级 | ✅ |
| [design/unified-member-model.md](./design/unified-member-model.md) | **统一成员模型**（2026-08 拍板并实施）：一类成员 + 世代更替，概念、9 条决策与代码锚点 | ✅ |
| [design/identity.md](./design/identity.md) | 主观身份与自组织社会（ADR 0024）：身份四正交层、联系簿、名册黄页化 | ✅ |
| [design/server-protocol.md](./design/server-protocol.md) | REST + SSE 协议 v2 的设计取舍 | ✅ |
| [design/webui.md](./design/webui.md) | WebUI（apps/webui）：React+Vite、/ui/ 静态托管、cyberpunk 视觉规范 | ✅ |
| [design/skills.md](./design/skills.md) | Skill 能力：开放标准 SKILL.md、三层目录、progressive disclosure | ✅ |
| [design/execution-device.md](./design/execution-device.md) | Execution device 设计哲学：能力阶梯、五条宪法、DockerRuntime 现状评估与加固方向 | ✅ |
| [design/telegram.md](./design/telegram.md) | Telegram 客户端（MVP）：单用户白名单、回复式收发、SDK 接入 | ✅ |
| [design/bridge-api.md](./design/bridge-api.md) | Bridge API：Python 机制底座 ↔ tlosure 策略层的桥——四类端口、版本纪律、四阶段迁移路线 | ✅ |
| [design/society-policy.md](./design/society-policy.md) | 社会政策语境：policy.tlon、pull 型求值点、数据物化注入、fitness/trust 政策数据化 | ✅ |
| [design/arena.md](./design/arena.md) | 演武场：staging 原型的动态行为评估——隔离宇宙架构、运行协议、语料、报告、成本界 | ✅ |
| [design/capabilities.md](./design/capabilities.md) | 能力数据化：复合能力成为 tlosure 函数 artifact——env 契约、纯度分区、staging 复用、词汇桥、退役纪律 | ✅ |
| [reference/tlosure-reference.md](./reference/tlosure-reference.md) | tlosure 语言核心：EDN 语法、求值器、燃料计量、特殊形式、通用词汇 | ✅ |
| [reference/dsl-reference.md](./reference/dsl-reference.md) | tlon 宿主侧：MemberSpec 全字段、领域 builtin、stage/section 清单 | ✅ |
| [reference/config-reference.md](./reference/config-reference.md) | agent.yaml、环境变量、`~/.tlon` 目录布局 | ✅ |
| [reference/tools-reference.md](./reference/tools-reference.md) | 全部工具的参数与语义 | ✅ |
| [reference/api-reference.md](./reference/api-reference.md) | REST/SSE 端点与事件帧格式 | ✅ |
| [guides/quickstart.md](./guides/quickstart.md) | uv tool install → tlon dream → WebUI 创建成员发信 | ✅ |
| [guides/writing-a-prototype.md](./guides/writing-a-prototype.md) | 手写一个 prototype 的完整走查 | ✅ |
| [decisions/](./decisions) | ADR：0001 EDN、0002 邮件唯一通道、0003 机制/策略分离、0004 stage 纯度、0005 SSE、0006 统一成员模型、0007 session 事件溯源、0008 上下文压力、0009 working memory 退役、0010 skill 能力、0011 SCI 兼容子集、0012 litellm 传输层、0013 admin token 鉴权、0014 环境-agent 协同演化、0015 prompt 缓存、0016 成员级 LLM 配置、0017 claim 记忆、0018 DSL 抽包 tlosure、0019 runtime/server/client 拆分、0020 Bridge API、0021 tlosure 完整函数能力、0022 社会政策语境、0023 token 预算停机线、0024 主观身份、0025 mail-history 两段式、0027 终止回复只发人类触发者、0028 收件箱驱动单 session、0029 认知成员转发走 forwardMail 工具 | ✅ |

✅ = 已写并与代码同步；📝 = 计划内未写。新增文档先登记进本表。