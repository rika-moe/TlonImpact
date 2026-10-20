# ADR 0006：统一成员模型——一类成员 + 世代更替

> P3c 当前实现：宿主能力统一定义为 Operation；Tool 与 DSL 共用定义和执行边界。旧工具名和类名仅记录当时设计，当前入口见 [工具手册](../reference/tools-reference.md)。
> 原型及新成员的技术 ID 与行为显示名由 [ADR 0035](0035-generated-ids-and-behavior-prototypes.md) 修订；历史职业名和 ID 示例不再是当前创建接口。

> 阅读定位：本文保留当时的选择与理由；当前设计见 [unified-member-model](../design/unified-member-model.md)、[prototypes](../design/prototypes.md)。当前适用范围：统一成员与世代模型；人格边界由 ADR 0024 澄清，档案保留不排除人类显式治理删除。完整关系见 [决策索引](README.md)。

状态：已接受（2026-08-16，方向讨论拍板）；阶段 1–3 已实施（2026-08，统一装配、档案归档），阶段 4（世代通道 `proposeBehavior`）已实施（2026-09）——进度见 [docs/design/unified-member-model.md](../design/unified-member-model.md)。后续：选择压（代谢经济学）与审批社会化的具体形态由 [ADR 0014](0014-environment-agent-coevolution.md) 拍板。


> 后续修订：[ADR 0042](0042-unified-member-rule-execution.md) 删除规则顶层 loop 与按执行方式分类的限制，使用统一 actions 和独立会话配方。

## 决策

**成员只有一种。** 同时拍板九条（完整定义与模型现状见 [docs/design/unified-member-model.md](../design/unified-member-model.md)）：

1. 成员使用同一种规则程序；执行格式由 [ADR 0042](0042-unified-member-rule-execution.md) 修订为 on/when/actions，会话作为普通效果。
2. **成本由实际调用决定**——程序可以只做普通计算，也可以按条件请求模型，不形成不同成员或规则类别。
3. **懒装配**——能力对象对所有成员统一构造（对象便宜），加载按需（调用才贵）。
4. **spec 不可直接修改，自己的也不行**——行为更新的唯一通道：创建新 prototype → 派生新 member。成员是基因型的纯表达，不反馈回基因型（世代更替，反拉马克）。
5. **允许 member 创建新 prototype**——`writePrototype`（写 staging）通道即行为演化的主通道，现状已具备。
6. **不能修改别人的 spec**——改他人行为只走提案通道。
7. **退休：停止是请求，接受与否是策略**——短期人类守护默认规则，规则本身是数据。
8. **退休成员档案归档**——`~/.tlon/{id}/` 永不删除；现存所有删除路径（contractor 回收、stopAgent、容器 force-remove）退场。
9. **原型种子知识暂不引入**。

## 理由

- **世代更替优于个体可塑性**（决策 4 的核心论证）：
  - 运行中热更新行为的所有信任难题（并发会话读新旧混合、回滚、审计）被整个绕开——因为不存在"运行中改"。
  - 进化的单位清晰：原型（可变的基因型）与成员（不可变的表达）在两个时间尺度上各自演化；成员生命周期（会话/记忆/退休）与物种生命周期（原型演化/评审/新世代）解耦。
  - 与书信共和国咬合：知识沉淀在信里、行为沉淀在原型里，个体只是流动的渡口；"学习的成果归种群，不归个体"正是社会进化的结构。
  - 信任闸按**影响半径**分级（改自己是危险的 → 干脆禁止；提案是安全的 → 走评审）。
- **归档而非删除**（决策 8）：书信共和国要求知识在书信中积累；删除 home = 知识随个体蒸发，与愿景直接冲突。不删是"保留策略"，治理（清理）是显式动作。
- **懒装配**（决策 3）：统一装配 + 按需加载，能力统一但成本可控；成本随实际调用发生。

## 备选方案

- **允许成员修改自己的 spec（个体可塑性）**：个体学习环直接闭合，无需出生新个体。被否：运行中改行为的一致性与回滚难题无解；且"学习成果归个体"使进化停留在个体层面，社会无法继承——与"进化发生在种群中"的愿景相悖。
- **原型种子知识**（决策 9 的备选）：让新世代带着父代要点出生。被否（暂缓）：知识继承通道（跨成员读取）尚未存在，种子知识没有落地形态；先做"档案归档"这一保留侧，继承侧等个体学习环接上后再议。

## 后果

- **代码面**：删 `member_type` / `AgentConfig.role` / `_derive_runtime` / `_discard_contractor` / `stop_contractor` 的 role 检查；统一装配 + 懒加载；`spawned_by` / `spawn_depth` / `prototype` 血缘字段保留为审计。模型现状与代码锚点见 [docs/design/unified-member-model.md](../design/unified-member-model.md)。
- **行为演化的主通道定型**：`writePrototype` → staging → 人类批准 → `spawnAgent` 派生新成员。人工审批是短期瓶颈（远期审批社会化，属路线"开放"一步）。
- **存储只增不减**：磁盘单调增长，短期人类手动清理，长期需治理规则（与决策 7 同源）。
- **文档同步义务**：本文与 [docs/design/unified-member-model.md](../design/unified-member-model.md) 是锚点；`design/dsl.md`、`design/runtime.md`、`design/prototypes.md`、`architecture.md`、`reference/config-reference.md`、`reference/tools-reference.md` 中的表述在代码实施时同步更新。
