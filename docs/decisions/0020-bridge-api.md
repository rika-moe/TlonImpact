# ADR 0020：Bridge API——机制底座与策略层之间桥的形式化

> 阅读定位：本文保留当时的选择与理由；当前设计见 [bridge-api](../design/bridge-api.md)、[Capability 删除决定](0045-remove-capabilities.md)。当前适用范围：桥的形式化；函数约束由 ADR 0021 修订，能力数据化的当前落地见 design。完整关系见 [决策索引](README.md)。

状态：已接受（2026-09-28，方向讨论拍板）

## 背景

[dsl.md](../design/dsl.md) 早已划清机制/策略边界：行为是数据（spec，LLM 可写），能力是代码（Python 白名单）。但连接两者的**桥本身是隐式的**——builtin 注册表、`ActionRequest` 效果通道、`ScriptHostQueries` 宿主端口、各语境的 env 注入，散在 [vocabulary.py](../../src/tlon/agents/vocabulary.py)、[member_runtime.py](../../src/tlon/agents/member_runtime.py)、[loop_driver.py](../../src/tlon/agents/loop_driver.py) 等处，没有统一视图。想往 tlosure 迁一处策略，作者无法回答"桥上有什么、缺什么该往哪加"。

讨论的起点是一个更大的提议：「大部分 runtime 内容改用 tlosure 编写，Python runtime 保持稳定」。评审结论是该提议的原始形态不可行且不可取——tlosure 被故意设计为受限语言（无函数/状态/异步/IO，fuel 封顶，见 dsl.md），用它承载调度、投递、沙箱等机制意味着逐项拆掉信任模型的地基；而能力层保持为代码是 dsl.md 明文拍板的选择。拍板的是修正版：**桥接面形式化为稳定的 Bridge API 层，策略/逻辑持续从 Python 向 tlosure 迁移；机制层永远留在 Python。**

## 决策

### 1. Bridge API = 四类端口的显式编目

- **查询端口**（host → DSL，只读）：协议类 + builtin 注册表工厂（`make_*_builtins(host)` 模式），只暴露拆包后的 plain data，不暴露宿主对象。
- **效果端口**（DSL → host，数据化意图）：`pure=False` 的动作 builtin 只产 `ActionRequest`，执行永远在引擎侧；动作位置纯度分区（`validate_member_action`）适用于一切新效果词汇。
- **环境契约**：每个求值语境（`:when`/`:actions`/on-complete/`:until`/stage `:if` 及未来新增）的可见符号清单与形状，显式定义、集中收拢。
- **语境注册表**：词汇按语境切分的纪律延续并编目；新增词汇必须声明所属语境。

### 2. 机制层清单封闭

EventBus、SessionScheduler/session 状态机、journal、MailDelivery、Mailbox、LLM/Embedding 客户端、MemoryManager 执行侧、DockerRuntime、EffectExecutor 安全校验、五个 stage 语义本体、tlosure 求值器——永远 Python。机制迁移的正当性只有"该逻辑该可进化"，没有"减少 Python 行数"。

### 3. 桥的版本纪律

~~词汇只增不删、语义只紧不松；新语境是桥的主要演化方式；桥编目文档从代码生成。项目"不留向后兼容"的总则适用于内部实现，**不适用于桥的词汇面**——spec 是落盘数据，桥的破坏式变更等于损坏存量 behavior 文件。~~

> 修订（2026-09-28，方向讨论拍板）：「只增不删」绝对化意味着坏设计永远留存，长期积累成词汇表的腐化。修订为 **deprecation 生命周期**：`introduced → deprecated → removed`。
>
> - **新增**：正常加词，不变；
> - **deprecated**：发现坏设计 → 标记废弃，加载期 warning（不拒载），生成文档标注，给迁移窗口；
> - **removed**：窗口后删除词汇，同时做磁盘 spec 的机械迁移——spec 语料小且全部可触达（仓内原型、Adam spec、`~/.tlon` 存量、staging 区），且 spec 有 `loads`/`dumps` 双向序列化，迁移是机械可测试的；loader 仍是唯一读盘路径，迁移发生在加载链上。
>
> 关键词汇面不再有「永不删除」的豁免——真正要防的从来不是删除，而是删除后磁盘 spec 悄悄坏掉，迁移正面解决了它。`:version` 字段留给 spec **格式**本身的变化（规则形态、字段结构），词汇增删不消耗版本号。只有当语料大到无法迁移（触达不到的外部作者）时才值得引入运行时版本矩阵——staging + 人工审批的闸门下语料永远可触达，这一天可能不会来；届时 deprecation 生命周期就是版本矩阵的降级特例。
>
> 不变的纪律：新语境仍是桥的主要演化方式；桥编目文档从代码生成；语义只紧不松（修复式收紧允许，加载期拒绝是唯一守门人）。


### 4. 四阶段迁移路线

① 桥形式化（行为不变）→ ② 存量策略数据化（记忆/反思参数、终止文案、Adam 消费健康事实）→ ③ 语言能力扩展评估（逐项拍板；闭包/宏/eval/异步明确不做，确需通用语言时按 ADR 0011 评估嵌入 SCI）→ ④ 能力数据化（远期，前置 dsl.md 末尾三个未拍板问题）。详见 [bridge-api.md](../design/bridge-api.md)。

## 后果

- 桥从隐式约定变为显式 API：策略迁移有了单一事实源，新增宿主能力有了固定通道与纪律。
- Python 机制底座的稳定成为架构目标而非偶然：策略演进不再触碰机制代码。
- 与 ADR 0018（tlosure 宿主无关）一致：桥的全部形式化在 tlon 侧；与 ADR 0011 一致：数据进出的端口纪律保证 spec 未来可移植 SCI 宿主。
- 代价：新增宿主能力多一步"是否该上桥、上哪个语境"的评审；桥文档需随代码生成维护，防漂移靠脚本而非自律。
- 本 ADR 不修改 dsl.md 的信任边界；阶段 4 触及该边界，届时先修订 dsl.md 再实施。
