# 原型与 spawn 体系设计

Tlon 的新成员不从代码里长出来，而从**原型**里派生：原型是一份纯数据的行为模板，spawn 是把它拷贝成一个真实 agent 的动作。这套体系是「新成员由社会内部产生」的当前形态。本文解释它为什么长现在这样，以当前实现为准（原型库在 `src/tlon/agents/prototypes.py`，内置原型在 `src/tlon/prototypes/`，spawn 工具在 `src/tlon/tools/agents/`）。

## 原型是什么

原型是一个**纯数据目录**，不含任何代码。判定标准只有一条：目录里有 `behavior.tlon`（`agents/prototypes.py` 的 `_is_prototype_dir`）。目录内容：

- `prototype.edn`——元数据 map：`:name`、`:display-name`、`:description`、`:trusted`、`:author`。缺省时以目录名兜底（`_read_metadata`）。
- `behavior.tlon`——一份**完整**的 MemberSpec（统一 rules 形态 `{:version 1 :rules [...]}`）。文件即全部行为，没有第二个来源。
- `IDENTITY.md` / `SOUL.md` / `RULES.md`——人格模板，可选，只对认知型成员有意义。

两个「不声明」值得注意：

- **不声明运行时类型**。成员是认知型（react）还是确定型（script）由 spec 的规则**派生**——任何规则带 `:loop` 即 react（`_derive_runtime`，调 `member_type`）。元数据里没有 `:runtime` 字段，因为派生值不需要存储，存储了反而可能和 spec 漂移。
- **不声明权限**。spec 是 total 的：工具能力、循环结构、上下文段全部写在 `behavior.tlon` 里，原型之外没有可叠加的东西。

元数据里 `trusted` 的默认值是 true：读不到 `:trusted` 键的目录视为可信。这不是疏忽而是信任模型的编码——能往 `~/.tlon/prototypes/` 写文件的是人类（或包内播种），手写即可信；只有 staging 区的写入路径会显式写 `:trusted false`。可信是默认，不可信必须显式声明，方向不能反——反过来的话，一次元数据读取失败就会把可信原型降级成不可用。

`src/tlon/prototypes/` 随包携带四个内置原型，恰好覆盖了体系的两个维度——认知/确定、重思考/轻响应：

| 原型 | 类型 | 关键 spec 差异 |
|---|---|---|
| archivist（档案管理者） | react | `:max-iterations 10`，反思间隔 5 分钟，知识注入全开——记忆整理型 |
| deep-thinker（深度思考者） | react | `:max-iterations 25`，stages 带 `(critique)` 与条件 `(revise {:if (critique-failed?)})`——多轮自评审 |
| fast-responder（快速反应者） | react | `:max-iterations 3`，`:context` 不含 `(knowledge)`、`(mail-history 5)`——精简上下文换速度 |
| relay（转发者） | script | 无 `:loop`，`:actions [(forward {:to ${to}})]`，唯一带 `:parameters` 的原型——零 token daemon |

三者认知型原型的差异全部写在 spec 数据里：迭代上限、stage 程序、上下文段、反思频率。没有一行 Python 区分它们——这正是「行为即数据」的演示：行为差异 = 数据差异，新成员类型就是新的一份 spec。

## 生命周期：播种 → 列出 → 实例化

### 播种：幂等，永不覆盖

`seed_prototypes`（`agents/prototypes.py`）在 `ServerApp.initialize()` 的装配早期运行（须在任何 agent 启动前——spawn 路径假设库已就位），把包内原型经 `importlib.resources` 拷贝到 `~/.tlon/prototypes/`。关键语义是**已存在的文件一律跳过**：用户在播种目录里的修改和新加的原型在重启后原样保留。播种是一次性「初始化资产」，不是同步——包内的更新不会冲掉用户的改动。这也解释了为什么 `list_prototypes` 读的是 `~/.tlon/prototypes/` 而不是包内目录：agent 看到的库就是用户手里的库，单一事实源。

### 为什么原型是目录而不是单文件

原型需要打包三类异质内容：EDN 元数据、EDN 行为 spec、Markdown 人格文件。单文件方案要么把 Markdown 嵌进 EDN 字符串（转义地狱，人格文件失去独立编辑性），要么发明新的打包格式。目录是零发明的答案：每种内容保持自己的格式与工具链，`behavior.tlon` 的存在与否天然成为判定谓词，「加一个原型」就是「拷一个目录」。这也与人格文件在 agent 侧的存储形态同构——原型目录和实例的 `config/` 目录结构一致，实例化因此是纯粹的目录内容拷贝。

### 列出与实例化

`listPrototypes` 工具（`tools/prototypes/list_prototypes.py` 的 `ListPrototypesTool`）把库的元数据暴露给 agent（名称、显示名、描述、trusted 标志）。`instantiate` 做真正的派生，语义是**拷贝，不是引用**：

1. 读原型的 `behavior.tlon`；声明了 `:parameters` 则先用调用方给的 `params` 渲染（见下节），未声明则必须为空 params——参数门是双向的：该给的没给拒绝，不该给的给了也拒绝（`render_template` 对未知参数直接报错）。模板契约是精确的，没有「多给了就忽略」的宽松。
2. 行为文件与人格文件逐字写入新 agent 的 `~/.tlon/{agent_id}/config/`——行为在创建时刻**冻结**，此后改原型不影响已派生的 agent。
3. `agent.yaml` 走与 wizard 相同的 `yaml_save` 路径写出，`prototype` 字段记录来源。
4. 无需重启生效：调用方（wizard 或注册表 spawn）热收养（`POST /admin/config/agents/{id}/reload` → `AgentRegistry.adopt_agent`）。

拷贝而非引用的理由与「spec 是唯一权威」一致：如果实例引用原型，改原型就会静默改变活着的 agent 的行为——行为数据有了第二真相，审计和复现都变得不可能。冻结拷贝让每个 agent 的 `config/` 目录在任何时候都是其行为的完整描述。

列出路径还有一条刻意的容错原则：**元数据读取不许失败**。`_read_metadata` 对坏 `prototype.edn` 回退目录名兜底；`_derive_runtime` 对解析不了的 `behavior.tlon` 回退 `"react"`；`_read_parameters` 失败回退空参数。理由是 listing 是发现入口——一个坏原型不该让整个库列表消失。代价是坏原型在列表里看起来正常（回退值恰好合理时），失败被推迟到实例化或启动——这不是假设，是已经发生的事实（见「已知问题」）。

## 模板：写侧专属的参数化

`behavior.tlon` 可以在顶层声明 `:parameters` map（如 relay 的 `{:to {:type :string-list :required true}}`），并在规则表单里用 `${to}` 引用。这层机制在 `agents/behavior_template.py`，核心性质是**写侧专属**：

- 运行时加载器看到 `:parameters` 只按未知键告警，从不解释 `${var}`——实例化在落盘前就把占位符渲染掉了，运行时永远见不到模板语法。
- `analyze` 解析声明并做结构校验：把 `${var}` 替换为类型对应的 stand-in（`""` 或 `[""]`）后试解析 EDN，得到 `standin_text`。这保证模板在**不执行任何东西**的前提下可被结构验证——校验器看到的是一份具体得足以过 schema 的文本。
- `render_template` 做真正的替换：必填缺失、未知参数、类型错误全部拒绝；值经 EDN `dumps` 序列化后替换进文本，产出合法 EDN。
- 声明与引用必须闭合：每个 `${var}` 都要在 `:parameters` 里声明，每个声明都要被引用。
- 声明本身有 schema：`:parameters` 是非空 map，每个参数只允许 `:type`（`:string` 或 `:string-list`，默认 `:string`）与 `:required`（默认 true）两个键，键名重复、未知键、未知类型全部在 `analyze` 阶段拒绝。参数类型刻意只有两种——够表达「一个地址」「一组地址」这类配置，不构成通用计算。

「写侧专属」是安全设计：模板语法只存在于 authoring 时刻，不进入 DSL 的运行时语义。DSL 保持受限、可停机，参数化没有给它增加任何表达能力——只是让同一份行为可以带着不同常量被拷贝多次。

以 relay 为完整例：它的 `behavior.tlon` 声明 `:parameters {:to {:type :string-list :required true}}`，规则体里是 `(forward {:to ${to}})`。spawn 时传 `params={"to": ["a@tlon.internal", "b@tlon.internal"]}`，渲染后落盘的实例文件里是 `(forward {:to ["a@tlon.internal" "b@tlon.internal"]})`——占位符消失，值已是序列化好的 EDN 字面量。同一个转发原型由此可以派生出转发目标各异的任意多个 relay 实例，而原型本身保持一份。

## spawn：从原型派生 contractor

agent 侧入口是 `SpawnAgentTool`（`tools/agents/spawn_agent.py`，工具名 `spawnAgent`）：指定原型名、显示名、可选模板参数，从原型派生一个 contractor（临时工）并立即启动。session 归属经 structlog contextvars 绑定——agent 的 session span 把 `session_id` 注入上下文变量，工具执行时取出作为配额桶；脱离 session 的直接调用（如测试）落入 spawner 的单一桶。这意味着配额记账不需要工具签名里多一个参数，session 是隐式上下文，LLM 看不见也伪造不了。

真正的管线在 `AgentRegistry.spawn_contractor`（`agents/agent_registry.py`），顺序是刻意安排的：**信任门先于一切预算检查**——`untrusted_prototype` 的拒绝在计数之前，staging 里的原型连预算都消耗不到。随后：

1. **预算检查**（见下节）。
2. **生成 id**——`c-{prototype}-{uuid8}`，由注册表生成、调用方无法指定，字符集收敛在 AgentConfig 允许的范围内（原型名先做字符清洗）。id 不让调用方指定，是为了让「contractor 是匿名的、一次性的」成为惯例：`c-` 前缀一眼可辨，uuid 后缀防碰撞，没有任何语义可供依赖。
3. **`instantiate`**——拷贝落盘（行为冻结）。
4. **contractor 身份**——`role="contractor"`、`spawned_by` 记录 spawner、`spawn_depth` 继承递增、空闲超时收紧为 `contractor_idle_timeout_seconds`（默认 300 秒，比 resident 短——临时工是可弃的）。
5. **注册并启动**，发布 `AGENT_SPAWNED`。

孤儿策略是**reparent 而非 kill**：contractor 独立于 spawner 注册，spawner 睡眠或停止不影响它，回收只由它自己的空闲超时决定。不选 kill 的理由：contractor 可能在为多个委托方工作（邮件寻址是开放的，任何人都能 @ 它），spawner 只是它的出生证明，不是它的所有者；所有权归注册表，生命周期归空闲事实——这与全系统「idle 是事实、drain 是策略」的分工一致。

### spawn 预算：内存账本

预算的三个硬编码默认值（注册表构造参数）：全局同时运行上限 `max_activations = 8`（resident 也算）、spawn 链深度 `max_spawn_depth = 3`（resident 深度 0）、每 (spawner, session) 配额 `session_spawn_quota = 2`。配额账本 `_spawn_quota` 只在内存里，重启清零——这是防失控机制，不是计量系统，重启丢失可接受。三个维度分别防三种失控：总量防资源耗尽，深度防无限递归 spawn，会话配额防单个任务循环里刷临时工。

拒绝经 `SpawnRejectedError` 带回机器可读的 `reason`（`max_activations` / `max_spawn_depth` / `session_quota` / `untrusted_prototype` / `config_reload_failed`），工具层把它翻译成 LLM 能理解的错误文案——`untrusted_prototype` 会明确告知「需人类在 mirror 中批准」，其余归为 `budget_exceeded`。机器可读的 reason 与面向 LLM 的文案分离，让程序与模型各取所需。

### 权限不可升格

spawn 不能授予任何新能力，这是两条结构性保证的合取：

- **来源唯一**——contractor 的 spec 只能来自原型库（trusted 那半边），LLM 不能临场写一份 spec 直接 spawn（它写的进 staging，见下节）。
- **spec total，无 overrides**——行为文件逐字拷贝，spawn 时没有任何参数能修改 spec 内容（模板参数只能填原型自己声明的槽位）。spawner 的能力上限就是它能用到的原型的上限。

### 销毁：容器强删与 home 删除

contractor 的终点有两条：`stopAgent`（`tools/agents/stop_agent.py` 的 `StopAgentTool` → `stop_contractor`，先做 drain 检查——有 ACTIVE/WAITING session 的拒绝停）或空闲超时收割（idle reaper 走同一条 `_discard_contractor`）。销毁做两件事：

1. **沙箱容器强删**——`DockerRuntime.remove(force=True)`。必须先于 home 删除执行，因为容器 bind-mount 了 home 下的 workspace。注册表侧为此缓存 container 句柄。
2. **删除 home 目录**——`~/.tlon/{id}/` 整个删掉。contractor 是可弃的，它的记忆、邮箱、workspace 随之消失。

对照组是 resident：睡眠或服务端关闭时**容器保留**（零成本唤醒、lazy 复用），home 目录更不会被碰。`stopAgent` 对 resident 直接拒绝（`resident_not_stoppable`）——resident 只能睡，永远不能经这条路径删除。这个不对称就是角色语义——resident 是社会的成员，contractor 是一次性的工具。

配套的可观测性是 `listAgents`（`tools/agents/list_agents.py` 的 `ListAgentsTool`）：列出全部注册 agent 的运行状态（running/sleeping/waking）与身份字段（role、来源 prototype、spawned_by）。spawn/stop 之前先 list，是工具设计上给 LLM 的标准用法——`spawnAgent` 成功回执里带回 `agent_id`、`spawn_depth` 等字段，供后续 `stopAgent` 或 @ 寻址使用。

## 信任分级：staging 闸

「agent 创造 agent」的安全闸是两区制：

- **trusted 区**（`~/.tlon/prototypes/`）——包内播种的与人类手写的原型，`:trusted true`，可直接 spawn。手写即可信：能写文件进这个目录的人本来就有系统的完全控制权。
- **staging 区**（`~/.tlon/prototypes-staging/`）——LLM 经 `writePrototype`（`tools/agents/write_prototype.py` 的 `WritePrototypeTool`）创作的原型落在这里，元数据强制 `:trusted false`、`:author` 记录作者 agent id（审计）。`spawnAgent` 拒绝 spawn 不可信原型。

`write_staging_prototype`（`agents/prototypes.py`）的铁律是**校验先于落盘**：模板声明校验 → EDN 解析 → `validate_member_spec` schema 校验（用无 host 谓词的默认注册表——host 谓词是 Adam 的词汇表，不许过闸），任何一步失败则什么都不写，错误原文返回给 LLM 让它改了重试。名称做字符集检查（字母数字加 `-`/`_`），且在两个区里都须唯一；`reject_prototype` 删除时还有路径穿越防护（resolve 后必须严格落在 staging 根下）。

晋级只由人类完成：`approve_prototype` 把目录从 staging 移入 trusted 库并把元数据改写为 `:trusted true`（`:author` 保留——晋级不抹掉作者，审计链不断）；`reject_prototype` 整个删除 staging 目录。这是 Gatekeeper 模型——LLM 可以创作行为，但只有人类能把创作变成可执行的成员。审批界面在 mirror 的「规约审批」；校验失败时给 LLM 的 ToolResult 里带 `hint`（schema 速查），让它能自助修正重试——闸要严，但试错成本要低。

`writePrototype` 支持两种 `spec_type`：`react`（认知规则 + 人格文件，默认）与 `script`（无 `:loop` 的确定性规则，零 token daemon，不需要人格）。两者输出的是同一种统一 rules 形态的 `behavior.tlon`——`spec_type` 只选择模板（写不写人格文件），不制造第二种文件格式。这让 staging 闸对两类成员是同一条校验链，没有旁路。

信任门与预算检查的顺序也是设计：`spawn_contractor` 里 `untrusted_prototype` 的拒绝排在一切预算检查**之前**。不可信原型连预算都消耗不到——否则 LLM 可以用不存在的权限反复试探配额边界，把拒绝回执当成预算状态的探针。

## 依赖倒置

spawn/stop/list/write 这些工具天然要触碰 agents 层（注册表）和 prototypes 层（原型库），但 import-linter 契约禁止 `tlon.tools` 反向依赖 `tlon.agents`。解法是端口声明在 tools 侧：

- `tools/agents/ports.py`——`AgentLifecycle` Protocol（`spawn_contractor` / `stop_contractor` / `get_all_agent_status` 的切片）加共享异常 `SpawnRejectedError` / `StopRejectedError`。
- `tools/prototypes/ports.py`——`PrototypeError`（原型库抛、工具捕）与 `PrototypeSummary`（只读元数据视图，`PrototypeInfo` 结构化满足）。

装配层（`AgentRegistry._assemble_cognitive`，`agents/agent_registry.py`）把实现注入工具实例：注册表把自己注入 `SpawnAgentTool` / `StopAgentTool` / `ListAgentsTool`（它结构化满足 `AgentLifecycle` Protocol，无须显式继承），把 `list_prototypes` / `write_staging_prototype` 作为普通 callable 注入 `ListPrototypesTool` / `WritePrototypeTool`。import 方向保持 tools → ports，永不倒流，由 `uv run lint-imports` 强制。

为什么不用直接 import 加契约豁免：反向依赖一旦开一个口子，tools 层就会逐渐长出对 agents 内部的随意引用，分层检查从「方向强制」退化为「逐个评审」。Protocol + 注入的额外收益是工具可独立测试——测试注入一个假的 `AgentLifecycle` 实现即可，不必拉起整个注册表。异常类型放 ports 而不是抛方模块，是因为 catch 发生在 tools 侧：异常是接口的一部分，不是实现的细节。

## 已知问题

如实记录一处当前缺陷：**archivist、deep-thinker、fast-responder 三个内置原型的 `behavior.tlon` 是非法 EDN**——第一条规则的 map 缺一个闭合 `}`（`:loop` 的 map 闭合后，规则 map 本身没有闭合就接了下一条规则），`edn.loads` 报 `EDNParseError: unmatched delimiter: ']'`。

这个 bug 被两层兜底掩盖了：

- 元数据读取不许失败：`_derive_runtime` 解析失败回退 `"react"`（恰好是正确答案），`_read_parameters` 失败回退空参数——所以 `listPrototypes` 看起来一切正常。
- `instantiate` 对无占位符的具体 spec 不做解析（`analyze` 只在有 `${var}` 时才试解析），所以实例化本身也不报错——坏文件被原样拷贝。

失败推迟到派生 agent 启动装配、加载 spec 时才暴露：这三个原型**实例化出的成员无法启动**，spawn 走到下游加载即失败。现有测试只覆盖了 relay（`src/tests/agents/test_relay.py`），三个认知型内置原型没有任何实例化测试——这是测试盲区，不是设计意图。relay 是合法模板（`analyze` 的 standin 文本可解析），不受此 bug 影响。

修复方向有两层，互相独立：直接修复是补上三处缺失的 `}`；系统性修复是给四个内置原型各加一个「播种 → 实例化 → 加载 spec」的冒烟测试（relay 的测试形态直接可复用），并在播种或列出时对 trusted 库做一次廉价的解析体检——容错原则保护的是 staging 与用户目录，包内携带的原型没有理由不可解析。

## 方向

与愿景的差距主要有两处：

- **实例化是纯拷贝，无变异算子**——「新成员从社会内部生长」目前只有复制一种算子：拷贝 + 填模板槽位。没有对 spec 的变异、重组、交叉，派生成员的多样性上限就是原型库的多样性。进化的原材料（可写的行为数据）已就位，但产生变异的机制还没有。
- **staging 审批是人工瓶颈**——Gatekeeper 模型里人类是每个 LLM 创作的原型的必经之路，这与愿景里「人类从审批者退为规则的守护者」相悖。自然的演化方向是把审批社会化：原型经邮件发给同行评议（书信共和国已有的通道），评议通过的原型按规则自动晋级，人类退到「定评议规则」的位置。
