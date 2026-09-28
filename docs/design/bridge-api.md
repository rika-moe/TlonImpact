# Bridge API：Python 机制底座与 tlosure 策略层之间的桥

> 方向已于 2026-09-28 拍板：**把桥接面形式化为稳定的 Bridge API 层，让策略/逻辑持续从 Python 向 tlosure 迁移；机制层（调度、IO、LLM、Docker）永远留在 Python。** 本文是该方向的设计文档：定义桥的形态、边界、版本约定与迁移路线。语言与 spec 的既有设计见 [dsl.md](./dsl.md)，本文不重复其论证，只在其基础上回答"桥怎么长"。

## 定位

Tlon 的愿景（[../vision.md](../vision.md)）要求行为是可进化的数据，机制是可信的代码。[dsl.md](./dsl.md) 已把这条线划清：行为即数据（spec 由 LLM 可写），能力仍是代码（白名单常量，人类维护）。但 dsl.md 只定义了桥两端各自的样子，**桥本身——宿主暴露什么、以什么形态暴露、如何演进——至今是散在代码里的隐式约定**：

- `Builtin(fn, pure)` 注册表（[tlosure/builtins.py](../../src/tlosure/builtins.py)）是注入通道，但词汇散在 [agents/vocabulary.py](../../src/tlon/agents/vocabulary.py) 的四个注册表里，没有统一的端口视图；
- `ActionRequest`（[tlosure/builtins.py](../../src/tlosure/builtins.py)）是效果返回通道，但"哪些动作存在、参数什么形状"只有读实现才知道；
- `ScriptHostQueries` 协议（[agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)）是宿主查询端口，但只有一个、且为 Adam 专用；
- env 注入（`{msg, event, self}` 等）分散在 [member_runtime.py](../../src/tlon/agents/member_runtime.py) / [loop_driver.py](../../src/tlon/agents/loop_driver.py) / [on_complete.py](../../src/tlon/agents/on_complete.py) 各处，每个语境能看到什么符号没有集中定义。

Bridge API 就是把这些隐式约定提升为**显式、集中、有版本契约的接口层**。它要回答的问题只有一个：当一处 Python 策略逻辑想迁往 tlosure 时，作者应该去哪里看"桥上有什么"、往哪里加"桥上还缺什么"。

## 边界：什么过桥的、什么永远不过桥

桥的存在不改变机制/策略的划分，它是这条划分的**载体**而非例外：

- **永远留在 Python（机制层）**：EventBus、SessionScheduler 与 session 状态机、journal、MailDelivery 投递路由、Mailbox、LLMClient/EmbeddingClient、MemoryManager 存储与检索执行、DockerRuntime、EffectExecutor 的安全校验（幻觉收件人、spawn 预算、信任闸）、五个 stage 的语义本体、tlosure 求值器本身。这些触碰并发/IO/外部世界，或是不可绕过的安全执行点——它们是 DSL 程序的"指令集"，必须是不可信环境里的可信不动点。
- **持续迁往 tlosure（策略层）**：何时醒、如何思考、完成后做什么（已是 spec）；以及目前仍是 Python 常量的调参型与措辞型政策（记忆检索权重、反思阈值、终止文案、社会事实的消费政策——见"迁移路线"阶段 2）。
- **桥自身**：端口的定义与实现在 Python，端口的清单与契约是文档化的 API。Python 侧端口实现遵循与 builtin 相同的纪律——纯查询无副作用，效果意图只产数据不执行。

## 桥的结构：四类端口

Bridge API 由四类端口组成，全部集中定义、集中编目。前两类已存在（本文做的是形式化而非发明），后两类是把现状中隐式的部分补上。

### 1. 查询端口（Queries，host → DSL，只读）

宿主状态的只读视图。现状：`ScriptHostQueries` 协议 + `make_member_builtins` 闭包注入（[agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)），生产实现是 [agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `RegistryQueries`。

形式化要求：

- 每个查询端口是一个 **协议（Protocol）类 + 一个 builtin 注册表工厂**（`make_*_builtins(host)` 模式），`pure=True`；
- 查询永不暴露宿主对象本身，只暴露拆包后的 Plain Old Data（关键字、字符串、数字、向量、map）——env 里的宿主对象保持不透明，没有属性/方法访问语法这条禁令（[dsl.md](./dsl.md)）不因桥而开口子；
- 查询的实现方（如 RegistryQueries）与 DSL 侧词汇各归各位：协议声明在词汇层，实现由机制组件提供，装配时注入。

### 2. 效果端口（Effects，DSL → host，数据化意图）

副作用的唯一合法形态：`ActionRequest` 数据，求值结束后由引擎执行。现状：六个动作 builtin（`spawn`/`emit`/`start`/`stop`/`start-all`/`forward`）。

形式化要求：

- 效果词汇永远 `pure=False`，产出已验证的 `ActionRequest`；参数校验全部前置在纯求值期（现状已如此，保持）；
- 新增效果端口 = 新增动作 builtin + 引擎侧 `_execute_action` 的一个分支 + 相应的配额/权限检查。效果端口的执行侧永远是机制层代码，DSL 够不到执行本身；
- 动作位置的纯度分区（`validate_member_action`：包装形式只允许 `when`/`while`/`map`，参数必须纯表达式）适用于一切新效果词汇，无例外。

### 3. 环境契约（Environments，每个语境的可见符号）

现状：env 构造散在各调用点——`{msg, event, self}`（member_runtime）、`{result}`（on_complete）、`{self, state, limits}`（loop until）、`{state, scratch, round}`（stage if）。

形式化要求：每个 DSL 求值语境（`:when` 门控、确定规则 `:actions`、on-complete、`:until`、stage `:if`，以及未来新增的语境）有一份**显式的环境契约**：可用符号清单、各符号的类型与形状、事件负载的字段约定。契约与语境注册表（下条）一一对应，共同构成"这个语境能表达什么"的完整答案。

### 4. 语境注册表（Contexts，词汇按语境切分）

现状已有雏形（`TRIGGER_BUILTINS` / `LOOP_BUILTINS` / `STAGE_COND_BUILTINS` / Adam 宿主谓词）。形式化要求：注册表的切分、每份注册表的内容、各语境的 fuel 预算分配方式，集中编目；新增词汇时必须声明它属于哪个（些）语境，禁止"哪里都能用"的词汇膨胀。

## 版本与兼容性约定

桥是 spec 作者（包括 LLM）面对的稳定界面，需要明确的演进纪律：

- **词汇只增不删、语义只紧不松**。既有 builtin 的名字、元数、返回形状不可变；修复 bug 式收紧（原来放行的坏输入改为拒绝）允许，但需在 changelog 记录。tlon 项目"不留向后兼容"的总规范适用于内部实现，**不适用于桥的对外词汇面**——spec 是落盘数据，桥上词汇的破坏式变更等于损坏所有存量 behavior 文件。
- **新语境 = 桥的小版本**。新增求值语境（含其注册表与环境契约）是桥的主要演化方式，比往既有语境里塞词更值得鼓励——语境切分是信任模型的组成部分。
- **文档从代码生成**。桥的完整编目（各语境的词汇、环境契约、fuel 预算）由脚本从注册表代码生成（沿用 [scripts/gen_reference_docs.py](../../scripts/gen_reference_docs.py) 的方式），文档与实现不可漂移。
- 加载期校验是唯一守门人：任何桥上词汇的引用错误都在加载时被 `validate_member_spec` 拒绝，运行期不存在"未定义词汇"这一类错误。

## 迁移路线

四阶段，每阶段独立交付、独立收尾；后一阶段的开始不以前序全部完成为前提（除标注的依赖外）。

### 阶段 1：桥的形式化（行为不变，纯整理）

把散落的隐式约定收拢为显式 API。不改任何运行时行为。

- 端口编目：盘点全部既有 builtin/动作/宿主谓词/env 注入点，按四类端口归类，形成单一事实源（建议落在 `agents/vocabulary.py` 的模块结构 + 生成文档）。
- 环境契约显式化：每个语境的 env 构造收拢到一处定义（目前散在三个文件），符号清单与形状写成类型化契约。
- 生成桥参考文档：`gen_reference_docs.py` 扩展出 bridge 编目（按语境列词汇、签名、fuel 默认、效果 kind）。
- 验收：行为零变化（既有测试全绿）；桥上有什么、每个语境能表达什么，不读实现代码即可回答。

### 阶段 2：存量策略数据化迁移（收益最大、风险最低）

把目前是 Python 常量的策略逐处迁移。每一项一个 PR，串行施工；多项共享一处文件时相邻施工。

- **Adam 消费健康事实**（首选示范，零 Python 改动）：`AGENT_NOISY` / `MAIL_ORPHAN_SCAN` 已由 [agents/health.py](../../src/tlon/agents/health.py) 机制化发布，但无策略消费。给 Adam spec 加规则即可，是"换政策不改引擎"的现成演示。
- **记忆参数数据化**：[memory/manager.py](../../src/tlon/memory/manager.py) 的检索权重 α/β/γ、冲突阈值 0.75、蒸馏窗口，从模块常量迁为配置数据。
- **反思参数数据化**：[agents/reflection.py](../../src/tlon/agents/reflection.py) 的触发阈值与 prompt 文本，阈值进 spec（`:reflect` 已是先例），prompt 落为可读文件。
- ~~**终止文案数据化**~~（已完成，2026-09-28）：[agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的三条固定终止回信措辞进 spec——落地形态是 `:loop` 内的 `:replies` 纯数据 map（键 `:iteration-limit` / `:error` / `:timeout`，加载期 total 校验），而非设想中的 `:on-limit` 动作。终止路径必须永不失败：措辞原文直接使用（不求值、不渲染），引擎内置常量保留为未声明键的兜底——DSL 化的是"默认措辞可被覆盖"，不是"措辞只能来自 spec"。
- 原则：迁移的正当性是"这部分逻辑该可进化/该由 LLM 写"，不是减少 Python 行数；机制层一行不动。

### 阶段 3：语言能力扩展（评估驱动，逐项拍板）

仅当阶段 2 后表达力确实不够时才启动。每项扩展独立评估其对信任模型的影响，拍板一项做一项：

- **低风险（纯词汇扩充）**：字符串处理（split/join/substring 等纯函数）、除法。只加词，不动语言结构，信任模型不受影响。
- **中风险（语言结构）**：命名函数（`defn` 或等价物）。解决 spec 重复书写，但递归随之可能、静态可枚举性下降——[dsl.md](./dsl.md) 记录过"无定义能力"的理由，扩之前需正面回应。
- **不做**：一等函数/闭包、宏、`eval`、可变全局状态、DSL 内异步。任何一项直接击穿 fuel 停机性与"加载时行为可枚举"的论证。若某天确实需要通用语言能力，按 ADR 0011 的既定退路评估嵌入 SCI，而不是把 tlosure 养成通用语言。

### 阶段 4：能力数据化（远期，对应愿景「开放」）

工具/能力描述成为数据 artifact，复用 spec 的 staging + 人工批准通道（[agents/prototypes/staging.py](../../src/tlon/agents/prototypes/staging.py) 模式）。前置是 [dsl.md](./dsl.md) 末尾三个未拍板问题：能力的数据化形态、信任分级、审计责任归属。回答之前不动工。

## 与既有决策的关系

- 本文不修改 [dsl.md](./dsl.md) 的信任边界（行为可进化、能力不可进化），桥是该边界内"策略侧继续扩大"的工程载体。
- 与 ADR 0018（tlosure 独立成包）一致：桥的全部形式化工作在 tlon 侧（词汇/端口/环境契约），tlosure 保持宿主无关。
- 与 ADR 0011（SCI 兼容子集）一致：桥的端口纪律（数据进出、无一等函数依赖）保证 spec 语义未来可移植到 SCI 宿主。
- 阶段 4 触及 dsl.md 明文的未拍板方向，届时需先修订 dsl.md 再实施。

## 非目标

- 不把 scheduler / MailDelivery / DockerRuntime / MemoryManager 等机制层用 tlosure 重写。
- 不追求"Python 行数最少"，追求"可进化的逻辑都在数据里、可信的机制都在代码里"。
- 不在桥上加通用 FFI / 任意 Python 对象通道；数据进、数据出，桥永远不传行为。
