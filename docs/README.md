# Tlon 文档地图

Agent 编写模块时，用现有 `file-read` 读取 workspace 内 `tlon/docs/guides/writing-a-module-program.md`，用 `file-list`/`file-read` 查阅 `tlon/stdlib/` 的真实源码。两个目录只读，随 runtime 发布；不需要 Docker，也没有专门的文档查询 API。普通 `tlon.authoring/draft` 通过这些工具查阅资料，用 `program-check` 校验并修订，最后由调用方决定是否 `prototype-write`。

新开发者从 [architecture](architecture.md) 开始，再阅读自己负责领域的 design；无需先遍历决策记录。目标与价值判断见 [vision](vision.md)，文档归属和更新流程见 [开发文档维护规则](documentation.md)。

## 按问题找文档

| 想了解什么 | 入口 |
|---|---|
| 系统目标与演化方向 | [vision](vision.md) |
| 系统概念、跨域约束、包/进程边界和数据所有权 | [architecture](architecture.md) |
| 当前某个领域如何工作、必须遵守哪些规则 | 下方领域设计 |
| 某次选择的背景、备选与代价 | [决策索引](decisions/README.md) |
| 精确字段、格式、接口与配置 | 下方参考文档 |
| 如何启动或完成一个具体任务 | 下方操作指南 |
| P5 升级、旧原型处置与行为变化 | [P5 发布说明](releases/p5.md) |

architecture 区分规范与当前实现；design 负责当前领域模型和机制；ADR 保留历史理由。已接受但未实现的方向单独标注，不能当作当前能力。目录中的文件表示内容归属，不代表未经复核即可保证每句话与代码一致。

## 领域设计

下面是阅读分组，不是新的软件分层。每个契约在所属领域完整定义，其他文档引用并说明如何遵守。

| 分组 | 文档 | 负责的问题 |
|---|---|---|
| 成员与演化 | [统一成员模型](design/unified-member-model.md) | 成员、原型与生命周期的基本关系 |
| 成员与演化 | [身份](design/identity.md) | 客观寻址、人格、联系簿与社会身份 |
| 成员与演化 | [原型](design/prototypes.md) | 提案、准入、实例化、spawn 与谱系 |
| 行为与执行 | [DSL](design/dsl.md) | 行为表达、语言安全与规则权威 |
| 行为与执行 | [Bridge API](design/bridge-api.md) | 宿主查询、效果、环境与语境边界 |
| 行为与执行 | [运行时](design/runtime.md) | 唤醒、模块入口、ProgramStore、历史回读与效果执行 |
| 行为与执行 | [基础设施](design/infrastructure.md) | 装配、EventBus、路径、配置与进程级观测 |
| 行为与执行 | [执行装置](design/execution-device.md) | L0/L1/L2、容器、文件和代码执行边界 |
| 通信与知识 | [邮件](design/mail.md) | 地址、邮局事务、邮箱、线程与已读语义 |
| 通信与知识 | [记忆](design/memory.md) | Claim、蒸馏、反思、检索和代际出口 |
| 治理与扩展 | [社会政策](design/society-policy.md) | 社会事实物化、政策 hook 与失败处理 |
| 治理与扩展 | [演武场](design/arena.md) | 隔离评估、语料、报告与审批边界 |
| 治理与扩展 | [Skills](design/skills.md) | SKILL.md 发现、索引、正文读取与信任边界 |
| 人类接入 | [服务端协议](design/server-protocol.md) | REST/SSE、客户端会话、通知与同步契约 |
| 人类接入 | [WebUI](design/webui.md) | 页面状态、邮箱同步、线程展示与管理交互 |
| 人类接入 | [Telegram](design/telegram.md) | SDK 接入、回复关联与失败处理 |

## 工作程序的分阶段替换（P1–P5 已合并，两个 EPIC 验收完成）

- [ADR 0046](decisions/0046-programmable-workflows.md)：选择普通函数工作程序，明确替代与非目标。
- [可编程工作流程](design/programmable-workflows.md)：module、Behavior、模型、工具、状态、调度和恢复边界。
- [分阶段实施计划](design/programmable-workflows-plan.md)：两个 EPIC 的完成矩阵、合并 PR、验收证据及历史阶段范围；当前[用法](guides/writing-a-module-program.md)。

## 共享操作的当前机制与后续范围

- [统一宿主操作](design/operations.md)：宿主契约与 DSL/LLM 入口；Capability 已删除。
- [分阶段实施计划](design/operations-plan.md)：两个串行 EPIC、分阶段独立 PR 任务及验收标准；P1–P6 已合并；P8a 独立收口文档与审计，其余范围单列。
- [执行作用域与共享额度](design/execution-scope.md)：已落地的 P4b 预算预留、取消/截止、声明顺序与可重入写边界。
- [调用记录与恢复](design/invocation-recovery.md)：已落地的 P4a 唯一叶子回执、冻结身份、unknown 与会话重放边界。
- [旧机制审计](design/operations-audit.md)：源码/测试证据、保留例外及未实施边界。
- [ADR 0044](decisions/0044-unified-operations.md)：接受方向与目标替代范围。已实现与暂停范围分别标注，精确接口见下方 reference。

## 参考文档与操作指南

| 文档 | 内容 |
|---|---|
| [tlosure-reference](reference/tlosure-reference.md) | EDN 语法、求值器、fuel、特殊形式与通用词汇 |
| [dsl-reference](reference/dsl-reference.md) | MemberSpec、领域词汇、stage/section 与政策格式 |
| [config-reference](reference/config-reference.md) | agent.yaml、环境变量、人格/记忆配置与目录布局 |
| [tools-reference](reference/tools-reference.md) | 工具参数与语义 |
| [operations-reference](reference/operations-reference.md) | P1–P6 共享操作契约、双入口、宿主领域与调用恢复 |
| [api-reference](reference/api-reference.md) | REST/SSE 端点、字段、错误与事件 |
| [quickstart](guides/quickstart.md) | 安装、启动、创建成员与发信 |
| [writing-a-prototype](guides/writing-a-prototype.md) | 编写一个行为原型 |

## 维护约定

- 中文叙述；代码标识符、EDN 关键字和 API 字段保持原文。
- 代码引用用文件路径与符号名，不依赖漂移的行号；本地相对链接应可解析。
- 新增或调整文档先明确职责并登记入口。改变当前设计时同步 architecture/design/reference，历史取舍通过 ADR 追溯。
- reference 不承载未实施规划；已完成施工记录保留在原 ADR 或 Issue/PR 的历史上下文，不继续累加到当前 design。
- reference 中生成的清单通过 `gen_reference_docs.py --check` 验证新鲜度。统一质量门为 `scripts/check_all.sh`；文档链接和跨文档语义仍需复核。
