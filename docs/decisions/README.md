# 决策记录索引

ADR 按历史编号保留，不按重要程度搬迁。理解当前系统先读 [architecture](../architecture.md) 与相关 design；本目录用于追溯选择的背景、备选和代价。文档更新流程见 [维护规则](../documentation.md)。

## 怎样阅读

- 下列分组是影响范围和领域入口，不是重要性排名；系统边界的变化通常需要跨域讨论，领域规则同样具有约束力。
- 「已接受」是当时的决策状态，不代表所有原文条款今天仍有效，也不代表方向已全部实现。
- 部分修订明确到条款；未列修订不等于逐句验证了历史正文。当前 design 负责解释今日有效机制及已知限制。
- 历史施工与验收记录保留在原文，不再把 ADR 当持续增长的交付日志；新验收记录写入 Issue/PR。
- 编号按现存历史保留，0026 缺号不补排，不据此推断存在另一份决策。

## 系统边界

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0001：用 Lisp 风格 EDN 作为行为 DSL 的语法](0001-edn-as-dsl.md) | [dsl](../design/dsl.md) | 语法选择保留；「无 fn/def」由 ADR 0021 修订，语言核心路径由 ADR 0018 调整 |
| [ADR 0002：邮件是唯一通信通道](0002-mail-as-only-channel.md) | [mail](../design/mail.md) | 邮件通信选择保留；当前投信契约由 ADR 0030 明确 |
| [ADR 0003：机制与策略分离——注册表纯机制，策略集中于 Adam](0003-mechanism-vs-policy.md) | [infrastructure](../design/infrastructure.md)、[society-policy](../design/society-policy.md) | 机制/策略分离保留；政策求值面由 ADR 0022 扩展 |
| [ADR 0004：stage 纯度——状态变更一律声明为 StageOutcome 数据](0004-stage-purity.md) | [runtime](../design/runtime.md) | Stage 纯度与效果数据化 |
| [ADR 0005：SSE 而非 WebSocket](0005-sse-over-websocket.md) | [server-protocol](../design/server-protocol.md) | REST + SSE 通信选择 |
| [ADR 0018：DSL 抽取为独立包 tlosure](0018-dsl-extraction-tlosure.md) | [dsl](../design/dsl.md)、[bridge-api](../design/bridge-api.md) | tlosure 语言核心独立于宿主 |
| [ADR 0019：tlon runtime / server / client 包级拆分](0019-runtime-server-client-split.md) | [infrastructure](../design/infrastructure.md)、[server-protocol](../design/server-protocol.md) | 包边界保留；facade 数量等历史快照以当前 architecture 为准 |

## 成员与演化

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0006：统一成员模型——一类成员 + 世代更替](0006-unified-member-model.md) | [unified-member-model](../design/unified-member-model.md)、[prototypes](../design/prototypes.md) | 统一成员与世代模型；人格边界由 ADR 0024 澄清，档案保留不排除人类显式治理删除 |
| [ADR 0014：环境-Agent 协同演化——代谢经济学与演化分层](0014-environment-agent-coevolution.md) | [prototypes](../design/prototypes.md)、[society-policy](../design/society-policy.md) | 共演化方向含未实施部分；已落地政策形态由 ADR 0022 具体化 |
| [ADR 0022：社会政策语境——policy.tlon 与 pull 型求值点](0022-society-policy-context.md) | [society-policy](../design/society-policy.md) | 社会政策语境与 pull 型求值点 |
| [ADR 0024：主观身份与自组织社会——人格自主权、联系簿与 all@ 退役](0024-subjective-identity.md) | [identity](../design/identity.md)、[unified-member-model](../design/unified-member-model.md) | 主观身份、联系簿、人格自主权与 all@ 退役 |
| [ADR 0034：统一成员寻址黄页与信息边界](0034-member-address-directory.md) | [identity](../design/identity.md)、[mail](../design/mail.md) | 修订 ADR 0024 黄页范围：覆盖所有已登记且可投递成员，黄页公开，私有材料边界保留 |
| [ADR 0035：系统生成标识与行为化原型库](0035-generated-ids-and-behavior-prototypes.md) | [prototypes](../design/prototypes.md)、[identity](../design/identity.md) | 原型／新成员系统 ID、行为显示名，合并相同行为，移除职业暗示与旧参数 |

## 运行时与上下文

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0007：Session 事件溯源——append-only 日志是 session 的唯一真相](0007-session-event-sourcing.md) | [runtime](../design/runtime.md) | 事件溯源保留；session 身份/触发语由 ADR 0028 修订，JSONL 物理存储由 ADR 0032 替代 |
| [ADR 0008：上下文压力管理——存储不动，视图分级减压](0008-context-pressure.md) | [runtime](../design/runtime.md)、[memory](../design/memory.md) | 减压模型保留；anchor 由 ADR 0017、唤醒模板由 ADR 0028 修订 |
| [ADR 0015：主动利用 LLM 前缀缓存——度量先行、稳定段靠前、显式断点](0015-prompt-caching.md) | [runtime](../design/runtime.md) | 前缀缓存与上下文排序选择 |
| [ADR 0023：Session 级 token 预算——认知循环的第二条停机线](0023-token-budget.md) | [runtime](../design/runtime.md) | Session token 预算停机线 |
| [ADR 0025：mail-history 两段式——冻结段 + 滑动段](0025-two-tier-mail-history.md) | [runtime](../design/runtime.md) | Mail-history 冻结段与滑动段 |
| [ADR 0027：终止回复只发给人类触发者](0027-termination-reply-human-only.md) | [runtime](../design/runtime.md) | 仅回复人类的抑制语义保留；单一触发者锚点由 ADR 0028 责任集替代 |
| [ADR 0028：收件箱驱动的单 session 模型](0028-inbox-driven-single-session.md) | [runtime](../design/runtime.md)、[mail](../design/mail.md) | 收件箱驱动单 session；替代旧来信队列/触发邮件模型 |
| [ADR 0032：SQLite session journal——事件与生命周期元数据同一事实存储](0032-sqlite-session-journal.md) | [runtime](../design/runtime.md)、[memory](../design/memory.md) | SQLite journal；替代 ADR 0007 的 JSONL 存储，保留事件溯源不变式 |

## 通信与记忆

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0009：Working Memory 退役——session journal 是唯一交互真相](0009-working-memory-retirement.md) | [memory](../design/memory.md)、[runtime](../design/runtime.md) | Working Memory 退役保留；摘要管线由 ADR 0017 部分替代，journal 存储由 ADR 0032 修订 |
| [ADR 0017：记忆系统重设计——claim 模型 + 向量检索 + JIT 注入，整体替换旧 episodic 记忆](0017-claim-based-memory.md) | [memory](../design/memory.md)、[runtime](../design/runtime.md) | Claim 模型与检索；journal 物理存储由 ADR 0032 修订 |
| [ADR 0029：认知成员转发走 forwardMail 工具](0029-forwardmail-tool.md) | [mail](../design/mail.md) | 转发归因、线程及引用选择保留；postmaster 失败通知由 ADR 0030 废止 |
| [ADR 0030：SQLite 邮局与明确的投信契约](0030-sqlite-post-office.md) | [mail](../design/mail.md) | SQLite 邮局、事务提交与明确回执；旧验收流水作为历史证据保留 |

## 行为与扩展

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0010：Skill 能力——开放标准 SKILL.md，三层目录，DSL `(skills)` section，模型自读正文](0010-skills.md) | [skills](../design/skills.md) | Skill 发现与渐进加载选择；当前正文访问限制见 design |
| [ADR 0011：DSL 语义演进锚定 SCI 兼容子集（为 JVM 迁移铺路）](0011-sci-compatible-subset.md) | [dsl](../design/dsl.md) | SCI 语义锚定方向；函数能力按 ADR 0021 扩展，不表示已迁移 JVM |
| [ADR 0020：Bridge API——机制底座与策略层之间桥的形式化](0020-bridge-api.md) | [bridge-api](../design/bridge-api.md)、[capabilities](../design/capabilities.md) | 桥的形式化；函数约束由 ADR 0021 修订，能力数据化的当前落地见 design |
| [ADR 0021：tlosure 完整函数能力——defn / 闭包 / 变长参数 / 递归 / 一等函数](0021-tlosure-full-functions.md) | [dsl](../design/dsl.md)、[bridge-api](../design/bridge-api.md) | 完整函数能力；替代旧的无定义能力及动作位置可枚举保证 |

## 接入与模型

| 决策 | 当前设计 | 有效范围与修订关系 |
|---|---|---|
| [ADR 0012：引入 LiteLLM 作为 LLM 传输层 + provider 预设（方案 B）](0012-litellm-llm-provider.md) | [infrastructure](../design/infrastructure.md) | LiteLLM 传输层保留；进程级 LLM 配置由 ADR 0016 替代，成员字段见配置参考 |
| [ADR 0033：本地单人 WebUI 身份与浏览器来源边界](0033-local-webui-identity.md) | [server-protocol](../design/server-protocol.md)、[webui](../design/webui.md) | 实例持久身份、同源 API、回环限定；替代 ADR 0013，关闭 #144 跨域入口 |
| [ADR 0013：admin 端点共享 token 鉴权（TLON_ADMIN_TOKEN）](0013-admin-token-auth.md) | [server-protocol](../design/server-protocol.md) | 已废止，由 ADR 0033 的本地来源边界替代 |
| [ADR 0016：LLM 配置成员级化——删除进程级 LLM_* 设置](0016-member-level-llm-config.md) | [infrastructure](../design/infrastructure.md)、[webui](../design/webui.md) | 成员级模型配置与不自动创建标准团队 |
| [ADR 0031：WebUI 邮箱同步、线程展示与人类已读管理](0031-webui-mail-consistency.md) | [webui](../design/webui.md)、[server-protocol](../design/server-protocol.md)、[mail](../design/mail.md) | 客户端同步及已读管理；旧验收流水作为历史证据保留 |
