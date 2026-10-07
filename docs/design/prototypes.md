# 原型与 spawn 体系设计

> 统一成员模型（ADR 0006，2026-08 实施）：成员只有一种；spawn 出的成员与手建成员同构、永不删除（停止即睡眠、档案归档）；`spawned_by`/`spawn_depth` 保留为血缘审计。模型总览见 [docs/design/unified-member-model.md](./unified-member-model.md)。

Tlon 的新成员不从代码里长出来，而从**原型**里派生：原型是一份纯数据的行为模板，spawn 是把它拷贝成一个真实 agent 的动作。这套体系是「新成员由社会内部产生」的当前形态。本文解释它为什么长现在这样，以当前实现为准（原型库在 [src/tlon/agents/prototypes.py](../../src/tlon/agents/prototypes.py)，内置原型在 [src/tlon/prototypes/](../../src/tlon/prototypes)，spawn 工具在 [src/tlon/tools/agents/](../../src/tlon/tools/agents)）。

## 原型是什么

原型是一个**纯数据目录**，不含任何代码。判定标准只有一条：目录里有 `behavior.tlon`（[agents/prototypes/info.py](../../src/tlon/agents/prototypes/info.py) 的 `_is_prototype_dir`）。目录内容：

- `prototype.tlon`——元数据 map：`:name`、`:display-name`、`:description`、`:trusted`、`:author`，以及谱系字段 `:parent`（父原型名，根缺省）、`:generation`（int，根=1，子=父+1，服务端推导）、`:proposal-note`（本代变更说明，自由文本，可缺省）。缺省时以目录名兜底（`_read_metadata`）；无谱系字段的旧文件读取时按根处理（parent=None、generation=1），不做落盘迁移。
- `behavior.tlon`——一份**完整**的 MemberSpec（统一 rules 形态 `{:version 1 :rules [...]}`）。文件即全部行为，没有第二个来源。
- `LESSONS.md`——**可选**：随包传递的教训文本——退休成员记忆的蒸馏产物（见 [memory.md](./memory.md)「退休蒸馏」），经 `writePrototype` / `write_trusted_prototype` / `update_prototype` 的 `lessons` 参数（或 admin REST 的 `lessons` 字段）写入；详情端点（`get_prototype_detail`）把文本带回，评审提案时可见。
- `REVIEW.md`——**可选**：staging 提案的同行评审记录，`reviewPrototype` 工具每次评审追加一条（时间、评审者 id、评审者模型、verdict、理由）；元数据读取不碰它（listing 零成本），approve 的整目录 `shutil.move` 把它免费带进 trusted 库；详情端点以 `reviews` 字段带回文本。

统一成员模型（ADR 0006）：原型**不带人格文件**——原型是行为的基因型，成员的人格文件（IDENTITY.md / SOUL.md / RULES.md）是**创建时的数据**（ops 创建时填写 / 未来工具注入），不是基因型的一部分。这也让进化的单位保持纯粹：能安全进化的只有受限 DSL（behavior.tlon），自由文本人格不进基因库。

两个「不声明」值得注意：

- **不声明运行时类型**。原型是行为模板——元数据里没有 `:runtime` 字段（`PrototypeInfo` 也不派生它）。一条规则带不带 `:loop` 是实例 spec 的语法事实，不构成任何类型标记。
- **不声明权限**。spec 是 total 的：工具能力、循环结构、上下文段全部写在 `behavior.tlon` 里，原型之外没有可叠加的东西。

元数据里 `trusted` 的默认值是 true：读不到 `:trusted` 键的目录视为可信。这不是疏忽而是信任模型的编码——能往 `~/.tlon/prototypes/` 写文件的是人类（或包内播种），手写即可信；只有 staging 区的写入路径会显式写 `:trusted false`。可信是默认，不可信必须显式声明，方向不能反——反过来的话，一次元数据读取失败就会把可信原型降级成不可用。

[src/tlon/prototypes/](../../src/tlon/prototypes) 随包携带十一个内置原型：

**角色行为原型**（快速开始的成员从它们创建——首启是空社会（ADR 0016），人类在 WebUI / ops 创建成员时选择原型与模型）：

| 原型 | 规则形态 | `:execution` 层 | 关键 spec 差异 |
|---|---|---|---|
| developer（开发工程师） | 带 `:loop` | L2 | 标准 ReAct 模板；`:max-iterations 100 :max-tokens 8000000`——研究类任务的迭代轮数退为安全网，token 预算是真实约束线（ADR 0023） |
| ops（运维工程师） | 带 `:loop` | L2 | 标准 ReAct 模板 |
| pm（项目经理） | 带 `:loop` | L1 | 标准 ReAct 模板 |
| support（客服专员） | 带 `:loop` | L0 | 标准 ReAct 模板 |
| product（产品经理） | 带 `:loop` | L1 | 标准 ReAct 模板 |
| data-analyst（数据分析师） | 带 `:loop` | L2 | 标准 ReAct 模板；数值同 developer |

**行为形态原型**（覆盖体系的两个维度——带不带 `:loop`、重思考/轻响应）：

| 原型 | 规则形态 | `:execution` 层 | 关键 spec 差异 |
|---|---|---|---|
| archivist（档案管理者） | 带 `:loop` | L0 | `:max-iterations 10 :max-tokens 1000000`，反思间隔 5 分钟，知识注入全开——记忆整理型 |
| deep-thinker（深度思考者） | 带 `:loop` | L0 | `:max-iterations 300 :max-tokens 10000000`——更长思考预算；stage 程序同默认（交卷前自评审） |
| fast-responder（快速反应者） | 带 `:loop` | L0 | `:max-iterations 10 :max-tokens 1000000`（同默认成员），差异在 `:context` 不含 `(knowledge)`、`(mail-history 5)`——精简上下文换速度 |
| scribe（书记员） | 带 `:loop` | L0 | 反思间隔 30 分钟——社会文摘的记录者（经提案通道入库的原型） |
| relay（转发者） | 无 `:loop` | L0 | `:actions [(forward {:to ${to}})]`，唯一带 `:parameters` 的原型——纯转发 |

`:execution` 层是执行装置能力声明（宪法一，[execution-device.md](./execution-device.md)「能力阶梯」——内置原型的定层表与「最低够用」原则记录在那里）：实例化时一次性投影进 agent.yaml 的 `docker.level`。「标准 ReAct 模板」指同一套 stage 程序（think + 交卷前 critique + act + revise）与 `:max-iterations 10 :max-tokens 1000000`（ADR 0023）。

所有原型的差异全部写在 spec 数据里：迭代上限、token 预算、stage 程序、上下文段、反思频率、规则形态。没有一行 Python 区分它们——这正是「行为即数据」的演示：行为差异 = 数据差异，新成员形态就是新的一份 spec。原型不带人格（ADR 0006）：成员的人格（IDENTITY/SOUL/RULES.md）在创建成员时经 WebUI / admin API 写入，是创建时数据，不是基因型。

## 生命周期：播种 → 列出 → 实例化

### 播种：幂等，永不覆盖

`seed_prototypes`（[agents/prototypes.py](../../src/tlon/agents/prototypes.py)）在 `ServerApp.initialize()` 的装配早期运行（须在任何 agent 启动前——spawn 路径假设库已就位），把包内原型经 `importlib.resources` 拷贝到 `~/.tlon/prototypes/`。关键语义是**已存在的文件一律跳过**：用户在播种目录里的修改和新加的原型在重启后原样保留。播种是一次性「初始化资产」，不是同步——包内的更新不会冲掉用户的改动。这也解释了为什么 `list_prototypes` 读的是 `~/.tlon/prototypes/` 而不是包内目录：agent 看到的库就是用户手里的库，单一事实源。

### 为什么原型是目录而不是单文件

原型需要打包异质内容：EDN 元数据、EDN 行为 spec、可选的 Markdown 教训文本（`LESSONS.md`）。单文件方案要么发明新的打包格式，要么把元数据塞进行为文本。目录是零发明的答案：每种内容保持自己的格式与工具链，`behavior.tlon` 的存在与否天然成为判定谓词，「加一个原型」就是「拷一个目录」——approve 的整目录 `shutil.move` 也因此免费带走一切随包文件。

### 列出与实例化

`listPrototypes` 工具（[tools/prototypes/list_prototypes.py](../../src/tlon/tools/prototypes/list_prototypes.py) 的 `ListPrototypesTool`）把库的元数据暴露给 agent（名称、显示名、描述、trusted 标志）。`instantiate` 做真正的派生，语义是**拷贝，不是引用**：

1. 读原型的 `behavior.tlon`；声明了 `:parameters` 则先用调用方给的 `params` 渲染（见下节），未声明则必须为空 params——参数门是双向的：该给的没给拒绝，不该给的给了也拒绝（`render_template` 对未知参数直接报错）。模板契约是精确的，没有「多给了就忽略」的宽松。
2. 行为文件逐字写入新 agent 的 `~/.tlon/{agent_id}/config/`——行为在创建时刻**冻结**，此后改原型不影响已派生的 agent。原型携带 `LESSONS.md` 时同样逐字拷贝到同一目录（与 behavior.tlon 为邻），新成员的 spec 用 `(lessons)` context section 显式声明才会把它注入对话；原型没有该文件则成员侧也不产生。
3. `agent.yaml` 走 `yaml_save` 标准路径写出，`prototype` 字段记录来源。`llm.model_name` 来自 `instantiate` 的必填 `model` 参数（ADR 0016——模型选择是部署策略，不是基因型；spec 语法已删除顶层 `:llm` 键，残留即加载报错）；没有进程级兜底，未声明模型的成员装配期硬报错。
4. 无需重启生效：调用方（ops 管理 API 或注册表 spawn）热收养（`POST /admin/config/agents/{id}/reload` → `AgentRegistry.adopt_agent`）。

拷贝而非引用的理由与「spec 是唯一权威」一致：如果实例引用原型，改原型就会静默改变活着的 agent 的行为——行为数据有了第二真相，审计和复现都变得不可能。冻结拷贝让每个 agent 的 `config/` 目录在任何时候都是其行为的完整描述。

列出路径还有一条刻意的容错原则：**元数据读取不许失败**。`_read_metadata` 对坏 `prototype.tlon` 回退目录名兜底；`_read_parameters` 对解析不了的 `behavior.tlon` 回退空参数。理由是 listing 是发现入口——一个坏原型不该让整个库列表消失。代价是坏原型在列表里看起来正常（回退值恰好合理时），失败被推迟到实例化或启动——这不是假设，是已经发生的事实（见「已知问题」）。

## 模板：写侧专属的参数化

`behavior.tlon` 可以在顶层声明 `:parameters` map（如 relay 的 `{:to {:type :string-list :required true}}`），并在规则表单里用 `${to}` 引用。这层机制在 [agents/behavior_template.py](../../src/tlon/agents/behavior_template.py)，核心性质是**写侧专属**：

- 运行时加载器看到 `:parameters` 只按未知键告警，从不解释 `${var}`——实例化在落盘前就把占位符渲染掉了，运行时永远见不到模板语法。
- `analyze` 解析声明并做结构校验：把 `${var}` 替换为类型对应的 stand-in（`""` 或 `[""]`）后试解析 EDN，得到 `standin_text`。这保证模板在**不执行任何东西**的前提下可被结构验证——校验器看到的是一份具体得足以过 schema 的文本。
- `render_template` 做真正的替换：必填缺失、未知参数、类型错误全部拒绝；值经 EDN `dumps` 序列化后替换进文本，产出合法 EDN。
- 声明与引用必须闭合：每个 `${var}` 都要在 `:parameters` 里声明，每个声明都要被引用。
- 声明本身有 schema：`:parameters` 是非空 map，每个参数只允许 `:type`（`:string` 或 `:string-list`，默认 `:string`）与 `:required`（默认 true）两个键，键名重复、未知键、未知类型全部在 `analyze` 阶段拒绝。参数类型刻意只有两种——够表达「一个地址」「一组地址」这类配置，不构成通用计算。

「写侧专属」是安全设计：模板语法只存在于 authoring 时刻，不进入 DSL 的运行时语义。DSL 保持受限、可停机，参数化没有给它增加任何表达能力——只是让同一份行为可以带着不同常量被拷贝多次。

以 relay 为完整例：它的 `behavior.tlon` 声明 `:parameters {:to {:type :string-list :required true}}`，规则体里是 `(forward {:to ${to}})`。spawn 时传 `params={"to": ["a@tlon.internal", "b@tlon.internal"]}`，渲染后落盘的实例文件里是 `(forward {:to ["a@tlon.internal" "b@tlon.internal"]})`——占位符消失，值已是序列化好的 EDN 字面量。同一个转发原型由此可以派生出转发目标各异的任意多个 relay 实例，而原型本身保持一份。

## spawn：从原型派生新成员

agent 侧入口是 `SpawnAgentTool`（[tools/agents/spawn_agent.py](../../src/tlon/tools/agents/spawn_agent.py)，工具名 `spawnAgent`）：指定原型名、显示名、可选模板参数，从原型派生一个新 agent 并立即启动。session 归属经 structlog contextvars 绑定——agent 的 session span 把 `session_id` 注入上下文变量，工具执行时取出作为配额桶；脱离 session 的直接调用（如测试）落入 spawner 的单一桶。这意味着配额记账不需要工具签名里多一个参数，session 是隐式上下文，LLM 看不见也伪造不了。

真正的管线在 `AgentRegistry.spawn_member`（[agents/agent_registry.py](../../src/tlon/agents/agent_registry.py)），顺序是刻意安排的：**信任门先于一切预算检查**——`untrusted_prototype` 的拒绝在计数之前，staging 里的原型连预算都消耗不到。随后：

1. **预算检查**（见下节）。
2. **生成 id**——`c-{prototype}-{uuid8}`，由注册表生成、调用方无法指定，字符集收敛在 AgentConfig 允许的范围内（原型名先做字符清洗）。`c-` 前缀一眼可辨，uuid 后缀防碰撞，没有任何语义可供依赖。
3. **`instantiate`**——拷贝落盘（行为冻结）。
4. **血缘审计**——`spawned_by` 记录 spawner、`spawn_depth` 继承递增（成员侧血缘；原型侧谱系见「谱系」一节）。不设任何角色标记：lazy/常驻由原型 spec 的 `:on :boot` 规则派生。
5. **注册并启动**，发布 `AGENT_SPAWNED`。

孤儿策略是**reparent 而非 kill**：新成员独立于 spawner 注册，spawner 睡眠或停止不影响它，回收只由它自己的空闲策略决定（sleep，不删）。理由：成员可能在为多个委托方工作（邮件寻址是开放的，任何人都能 @ 它），spawner 只是它的出生证明，不是它的所有者；所有权归注册表，生命周期归空闲事实——这与全系统「idle 是事实、drain 是策略」的分工一致。

### spawn 预算：内存账本

预算的三个硬编码默认值（注册表构造参数）：全局同时运行上限 `max_activations = 8`、spawn 链深度 `max_spawn_depth = 3`、每 (spawner, session) 配额 `session_spawn_quota = 2`。配额账本 `_spawn_quota` 只在内存里，重启清零——这是防失控机制，不是计量系统，重启丢失可接受。三个维度分别防三种失控：总量防资源耗尽，深度防无限递归 spawn，会话配额防单个任务循环里刷成员。

拒绝经 `SpawnRejectedError` 带回机器可读的 `reason`（`max_activations` / `max_spawn_depth` / `session_quota` / `untrusted_prototype` / `config_reload_failed`），工具层把它翻译成 LLM 能理解的错误文案——`untrusted_prototype` 会明确告知「需人类在 ops 中批准」，其余归为 `budget_exceeded`。机器可读的 reason 与面向 LLM 的文案分离，让程序与模型各取所需。

### 权限不可升格

spawn 不能授予任何新能力，这是两条结构性保证的合取：

- **来源唯一**——新成员的 spec 只能来自原型库（trusted 那半边），LLM 不能临场写一份 spec 直接 spawn（它写的进 staging，见下节）。
- **spec total，无 overrides**——行为文件逐字拷贝，spawn 时没有任何参数能修改 spec 内容（模板参数只能填原型自己声明的槽位）。spawner 的能力上限就是它能用到的原型的上限。

### 停止与排空：睡，永不删

统一成员模型（ADR 0006）：成员只有一种，`stopAgent`（[tools/agents/stop_agent.py](../../src/tlon/tools/agents/stop_agent.py) 的 `StopAgentTool` → `stop_member`，先做 drain 检查——有 ACTIVE/WAITING session 的拒绝停）与空闲超时收割（idle reaper 走 `_drain_agent`）对**所有**成员一视同仁：停止即睡眠（`AGENT_STOPPED` / `AGENT_SLEEPING`），home 目录永不删除——人格、记忆、邮箱、workspace 全部归档，之后可被唤醒。容器也从不 force-remove（睡眠保留容器，零成本唤醒）。系统内（agent 侧与自动路径）不存在"删除成员"这条路径：成员只会"睡"，不会"蒸发"；唯一的删除通道是人类治理的显式 admin 端点（`DELETE /admin/config/agents/{agent_id}`）。

配套的可观测性是 `listAgents`（[tools/agents/list_agents.py](../../src/tlon/tools/agents/list_agents.py) 的 `ListAgentsTool`）：列出全部注册 agent 的运行状态（running/sleeping/waking）与血缘字段（来源 prototype、spawned_by）。spawn/stop 之前先 list，是工具设计上给 LLM 的标准用法——`spawnAgent` 成功回执里带回 `agent_id`、`spawn_depth` 等字段，供后续 `stopAgent` 或 @ 寻址使用。

## 信任分级：staging 闸

「agent 创造 agent」的安全闸是两区制：

- **trusted 区**（`~/.tlon/prototypes/`）——包内播种的与人类手写的原型，`:trusted true`，可直接 spawn。手写即可信：能写文件进这个目录的人本来就有系统的完全控制权。
- **staging 区**（`~/.tlon/prototypes-staging/`）——LLM 经 `writePrototype`（[tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py) 的 `WritePrototypeTool`）创作的原型落在这里，元数据强制 `:trusted false`、`:author` 记录作者 agent id（审计）。`spawnAgent` 拒绝 spawn 不可信原型。

`write_staging_prototype`（[agents/prototypes/staging.py](../../src/tlon/agents/prototypes/staging.py)）的铁律是**校验先于落盘**：模板声明校验 → EDN 解析 → `validate_member_spec` schema 校验（用无 host 谓词的默认注册表——host 谓词是 Adam 的词汇表，不许过闸），任何一步失败则什么都不写，错误原文返回给 LLM 让它改了重试。名称做字符集检查（字母数字加 `-`/`_`），且在两个区里都须唯一；`reject_prototype` 删除时还有路径穿越防护（resolve 后必须严格落在 staging 根下）。`writePrototype` 可带可选的 `parent`/`proposal_note` 参数记录谱系（见下节）：给了 `parent` 时校验父原型存在（trusted 或 staging 皆可——staging 提案可以继续派生提案），`:generation` 由服务端推导为父代 + 1，客户端无权指定。`lessons` 参数（可选）把随包教训落盘为 `LESSONS.md`——退休成员蒸馏出的经验由此搭上下一代的出生包。

晋级只由人类完成：`approve_prototype` 把目录从 staging 移入 trusted 库并把元数据改写为 `:trusted true`（`:author` 保留——晋级不抹掉作者，审计链不断）；`reject_prototype` 整个删除 staging 目录。这是 Gatekeeper 模型——LLM 可以创作行为，但只有人类能把创作变成可执行的成员。审批界面在 ops 的 Agents 视图（`s`）与 WebUI 的 PROTOTYPES 视图；校验失败时给 LLM 的 ToolResult 里带 `hint`（schema 速查），让它能自助修正重试——闸要严，但试错成本要低。

人类手写有直达入口（WebUI 的 PROTOTYPES 视图，`POST /admin/prototypes` → `write_trusted_prototype`）：直接写进正式库、`:trusted true`、无 `:author`——handwritten implies trusted，与元数据读取的默认方向一致；写前跑的是与 staging 完全相同的校验链（`_validate_behavior`），失败不落盘。编辑（`update_prototype`，`PATCH /admin/prototypes/{name}` 及 staging 变体）可改 `display-name`/`description`、`lessons`（重写 `LESSONS.md`，不传则保留）和/或重写 `behavior.tlon`：behavior 变更先跑校验链、失败则磁盘原样，元数据改写保留既有 `:trusted`/`:author`（于是 staging 原型可以在审批前被人类修正，晋级语义不变）。删除分两侧：正式库走 `delete_prototype`（`DELETE /admin/prototypes/{name}`，与 `reject_prototype` 同款路径穿越防护），staging 的删除即 reject。

`writePrototype` 对带不带 `:loop` 的规则形态输出同一种统一 rules 形态的 `behavior.tlon`，不写人格文件。staging 闸对两种形态是同一条校验链，没有旁路。

提案资格还有第二道闸——血脉偿付门槛（agent 侧）：`writePrototype` / `proposeBehavior` 在执行开头检查作者血脉的偿付能力（`agents/policy.py` 的 `solvent?` 政策 hook，ADR 0022——默认政策：总消耗 ≥ 10 万 token 且效率 < 全体均值 × 0.25 才算失去偿付能力，样本不足无罪推定，阈值保守宁可放过；公式与阈值可在 `~/.tlon/policy.tlon` 调整），失去偿付能力的血脉提案被直接拒绝（staging 无残留），指引是「先用手上成员做出被 endorse 的产出再提案」。这道闸不进 `write_staging_prototype` 域函数：admin REST 的人类直写不受闸——信任闸按影响半径分级，人类不受影响半径约束。闸本身 fail-open：偿付派生任何异常都放行。

第三道闸是近亲拒绝（ADR 0014 的非收敛选择压，agent 侧）：提案文本与 trusted + staging 两区所有既有原型的 `behavior.tlon` 做纯文本相似度比较（`find_near_kin`，`difflib.SequenceMatcher.ratio`，比较前规范化——去掉 `;` 注释行、折叠空白；不解析 EDN，模板原型的 `${var}` 占位符按原文比），最相似的一档 ≥ 阈值 0.9 就直接打回（staging 无残留），指引是「做出实质差异或改为提案改进既有原型」。阈值刻意定高、只拦换皮级雷同与**跨血脉**趋同：提案的声明父本在比较中豁免（NEAT 式物种形成保护——创新在自己血脉内被保护几代；writePrototype 的豁免名取显式 `parent`、缺省回退作者自身的 prototype，proposeBehavior 的父本恒为成员自己的 prototype），于是沿血脉的代际微改放行——实测锚点：scribe 底稿仅把 reflect 间隔 15→30 的微改相似度 0.996，父本豁免后放行、跨血脉仍被拦；实质重写（换谓词/动作/规则结构）实测落在 0.3 量级，不受影响。与偿付闸同款边界：不进 `write_staging_prototype`、不闸 admin REST 人类直写，fail-open——相似度扫描任何异常都放行。远期档（MAP-Elites 生态位档案）未做。

三道闸之外是**同行评审记录**：任何成员可用 `reviewPrototype`（[tools/prototypes/review_prototype.py](../../src/tlon/tools/prototypes/review_prototype.py) 的 `ReviewPrototypeTool`）评审一个 staging 提案。调用方只点名——verdict 不由调用方当场给，工具内部用**评审者自己的** `llm_client` 做一次专项评审调用（`purpose="review"`、timeout 60s；采样参数不配置，供应商默认生效），材料是提案的 behavior 全文、随包 lessons、谱系链与每代 diff；响应首行必须是三个标记之一（`通过`/`不通过`/`弃权`），不合规则 `success=False` 且不写记录。通过的评审追加进提案目录的 `REVIEW.md`（每条含时间、评审者 id、评审者模型 `llm_client.config.model_name`、verdict、理由全文）。异质性来自评审者的模型血统（成员的模型在创建时经 WebUI/admin API 选定，随 spawn 沿血脉继承，ADR 0016——不同血脉各自绑定不同模型）——评审记录格式统一，判断却出自不同血统，这正是 ADR 0014 的非收敛选择压落在审批环节的形态。评审是**咨询性的**：不闸 approve，人类审批时在详情 payload 的 `reviews` 字段里读到全部记录再做决定；评审记录随 approve 的整目录 move 晋级 trusted，审计链不断。

评审材料还包括执行能力声明的派生红旗（宪法一，[execution-device.md](./execution-device.md)）：spec 顶层 `:execution` 段声明的层级随提案一起评审——staging 提案声明 `:L2`（Docker 完整 Linux userland）本身就是红旗，详情 payload 派生 `execution_level` + `red_flags`（`execution-level-l2`），WebUI PROTOTYPES 视图渲染为警示徽章；`reviewPrototype` 的评审 prompt 角度清单也含对应一条（L0/L1 足以满足却声明 L2 是红旗）。

信任门与预算检查的顺序也是设计：`spawn_member` 里 `untrusted_prototype` 的拒绝排在一切预算检查**之前**，紧随其后是同一偿付门槛的 spawn 侧（`insolvent_lineage`：失去偿付能力的血脉连 spawn 资格也没有）。不可信原型连预算都消耗不到——否则 LLM 可以用不存在的权限反复试探配额边界，把拒绝回执当成预算状态的探针。

## 谱系：原型谱系作为一等数据

成员侧的血缘早已落盘（`agent.yaml` 的 `prototype`/`spawned_by`/`spawn_depth`），原型侧此前没有——一个原型从哪个原型派生、改了什么，只能靠人脑记。本节把原型谱系提升为一等数据：

- **落盘的只有三个元数据字段**：`:parent`（父原型名）、`:generation`（代数，根=1、子=父+1，永远服务端推导）、`:proposal-note`（本代变更说明）。写入点只有 `write_staging_prototype` / `write_trusted_prototype` / `update_prototype` 三处；`approve_prototype` 的晋级是目录搬迁 + 元数据改写，谱系字段原样保留（审计链不断）。
- **谱系查询是派生的，不另落盘**：`get_prototype_lineage` 沿 `:parent` 链向上走到根，返回每一代的 `{name, generation, author, proposal_note, trusted}` 加相邻两代 `behavior.tlon` 的 unified diff（difflib 文本 diff，不做结构化 diff——模板 `${var}` 占位符会让 EDN loads 失败，而 behavior.tlon 本身已是规范化文本）。父原型被删除/改名时链断在哪就停在哪，最老可达的一代标注 `broken`；环与失控链由 visited 集合 + 链长上限（32）截断，同样标注 `broken`。
- **两个查询面**：agent 侧有 `getPrototypeLineage` 工具（[tools/prototypes/get_lineage.py](../../src/tlon/tools/prototypes/get_lineage.py)），`listPrototypes`/`listStagingPrototypes` 的输出也带上了 `parent`/`generation`；人类侧 `GET /admin/prototypes/{name}`（含 staging 变体）的详情 payload 内嵌同一份 lineage 结果，`POST`/`PATCH` 接受 `parent`/`proposal_note`（`generation` 不接受客户端指定）。

已知限制：同名原型原地 `update_prototype` 改写 behavior 不产生新代——代际只在「创建新原型」时推进，原地编辑会造成同一 `generation` 下行为混杂。这是刻意的简化：代际语义绑定在派生动作上，不绑定在编辑动作上。

## 依赖倒置

spawn/stop/list/write 这些工具天然要触碰 agents 层（注册表）和 prototypes 层（原型库），但 import-linter 契约禁止 `tlon.tools` 反向依赖 `tlon.agents`。解法是端口声明在 tools 侧：

- [tools/agents/ports.py](../../src/tlon/tools/agents/ports.py)——`AgentLifecycle` Protocol（`spawn_member` / `stop_member` / `get_all_agent_status` 的切片）加共享异常 `SpawnRejectedError` / `StopRejectedError`。
- [tools/prototypes/ports.py](../../src/tlon/tools/prototypes/ports.py)——`PrototypeError`（原型库抛、工具捕）与 `PrototypeSummary`（只读元数据视图，`PrototypeInfo` 结构化满足）。

装配层（`assemble_cognitive`，[agents/assembly.py](../../src/tlon/agents/assembly.py)）把实现注入工具实例：注册表把自己注入 `SpawnAgentTool` / `StopAgentTool` / `ListAgentsTool`（它结构化满足 `AgentLifecycle` Protocol，无须显式继承），把 `list_prototypes` / `write_staging_prototype` 作为普通 callable 注入 `ListPrototypesTool` / `WritePrototypeTool`。import 方向保持 tools → ports，永不倒流，由 `uv run lint-imports` 强制。

为什么不用直接 import 加契约豁免：反向依赖一旦开一个口子，tools 层就会逐渐长出对 agents 内部的随意引用，分层检查从「方向强制」退化为「逐个评审」。Protocol + 注入的额外收益是工具可独立测试——测试注入一个假的 `AgentLifecycle` 实现即可，不必拉起整个注册表。异常类型放 ports 而不是抛方模块，是因为 catch 发生在 tools 侧：异常是接口的一部分，不是实现的细节。

## 已知问题

~~一处当前缺陷：**archivist、deep-thinker、fast-responder 三个内置原型的 `behavior.tlon` 是非法 EDN**——第一条规则的 map 缺一个闭合 `}`（`:loop` 的 map 闭合后，规则 map 本身没有闭合就接了下一条规则），`edn.loads` 报 `EDNParseError: unmatched delimiter: ']'`。~~ **已修复**（三处补齐 `}`，且新增 [src/tests/agents/test_builtin_prototypes.py](../../src/tests/agents/test_builtin_prototypes.py) 对四个内置原型做「播种 → 实例化 → 加载 spec」冒烟测试，解析 + 单一校验器全过，防止同类回归）。

当时这个 bug 被两层兜底掩盖了：

- 元数据读取不许失败：`_derive_runtime` 解析失败回退 `"react"`（恰好是正确答案），`_read_parameters` 失败回退空参数——所以 `listPrototypes` 看起来一切正常。
- ~~`instantiate` 对无占位符的具体 spec 不做解析（`analyze` 只在有 `${var}` 时才试解析），所以实例化本身也不报错——坏文件被原样拷贝。~~ 这层兜底已不存在：`instantiate` 对渲染后 spec 做结构化解析（`MemberSpec.from_edn`，最初为读取 `:llm` 声明引入；ADR 0016 后 `:llm` 已从语法删除，解析保留为纯校验门），解析失败以 `PrototypeError` 拒绝实例化，坏文件不再被原样拷贝。

失败推迟到派生 agent 启动装配、加载 spec 时才暴露：这三个原型**实例化出的成员无法启动**，spawn 走到下游加载即失败。当时现有测试只覆盖了 relay（[src/tests/agents/test_relay.py](../../src/tests/agents/test_relay.py)），三个带 `:loop` 的内置原型没有任何实例化测试——这是测试盲区，不是设计意图。relay 是合法模板（`analyze` 的 standin 文本可解析），不受此 bug 影响。

系统性修复有两层，互相独立：直接修复是补上三处缺失的 `}`（已完成）；回归防护是给四个内置原型各加一个「播种 → 实例化 → 加载 spec」的冒烟测试（已完成，relay 的测试形态直接复用）。「播种/列出时对 trusted 库做解析体检」暂未做——容错原则保护的是 staging 与用户目录，包内携带的原型由冒烟测试兜底。

## 方向

与愿景的差距主要有两处：

- **实例化是纯拷贝，无变异算子**——「新成员从社会内部生长」目前只有复制一种算子：拷贝 + 填模板槽位。没有对 spec 的变异、重组、交叉，派生成员的多样性上限就是原型库的多样性。进化的原材料（可写的行为数据）已就位，但产生变异的机制还没有。
- **staging 审批是人工瓶颈**——Gatekeeper 模型里人类是每个 LLM 创作的原型的必经之路，这与愿景里「人类从审批者退为规则的守护者」相悖。审批社会化的第一步已落地：成员可用 `reviewPrototype` 对 staging 提案做异质同行评审，评审记录（含评审者模型血统）落盘 `REVIEW.md` 并随详情端点呈现给审批者——人类不再是唯一读提案的人。但**自动晋级仍不做**：评审是咨询性的，approve 依旧是人类决定；下一步才是「评议通过的原型按规则自动晋级」，人类退到「定评议规则」的位置。