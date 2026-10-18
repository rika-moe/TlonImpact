# 手写一个 Prototype

完整走查：从零写一个行为原型，把它变成活的 agent。读完你就能：

- 手写一份 `behavior.tlon`（rules-form EDN），经 WebUI 创建并取得系统原型 ID；
- 用 WebUI 或 `spawnAgent` 从原型派生 agent；
- 走一遍「LLM 创作 → staging → 人工审批」的完整管线。

前置：先通读 `design/prototypes.md`（为什么这么设计）、`reference/tlosure-reference.md`（EDN 语法与求值器）与 `reference/dsl-reference.md`（MemberSpec 全字段与领域词汇）。本文是操作手册，不重复语法。

## 1. 原型长什么样

原型是一个**纯数据目录**，判定标准只有一条：目录里有 `behavior.tlon`（[agents/prototypes/info.py](../../src/tlon/agents/prototypes/info.py) 的 `_is_prototype_dir`）。

```
~/.tlon/prototypes/{prototype_id}/
├── prototype.tlon          # 元数据：:name / :display-name / :description / :trusted / :author
│                          #   + 谱系：:parent / :generation / :proposal-note
└── behavior.tlon          # 成员的全部行为规约（total spec，唯一权威）
```

谱系字段（可缺省）：`:parent` 是父原型 ID（根原型不写）；`:generation` 是代数，根=1、子=父+1，**永远由服务端推导**，手写时不必填写（读取缺省按根处理）；`:proposal-note` 是本代变更说明的自由文本。写新原型时若它是某个既有原型的改进版，在创建表单或请求中填上现有父原型 ID 即可挂上谱系——之后 `getPrototypeLineage` / `GET /admin/prototypes/{name}` 能展示整条代际链与每代的行为 diff。

原型**不带人格文件**（统一成员模型，ADR 0006）：原型是行为的基因型；成员的人格（IDENTITY/SOUL/RULES.md）是创建时的数据，不是基因型的一部分——创建成员时经 WebUI / admin API 的 `persona` 字段写入。

两个关键约束（写之前先记住）：

- **`behavior.tlon` 是文件即全部行为**。工具能力、循环结构、上下文段全部写在 spec 里，原型之外没有可叠加的东西，spawn 时无法 override。
- **原型不声明运行时类型**。成员的规则带不带 `:loop` 由 spec **派生**——带 `:loop` 的规则挂认知引擎，无 `:loop` 的规则命中后直接执行动作；带不带 `:loop` 决定一切。

## 2. 从零写一个带 `:loop` 的原型

带 `:loop` = 有 LLM 参与、跑认知循环的成员。我们写一个「短轮次回复」行为：收到点名自己的邮件就做一轮简短的认知循环（think/act + 交卷前自评审），最多 3 轮。

### 2.1 行为显示名与系统元数据

在 WebUI 的 PROTOTYPES → CREATE 中填写显示名「短轮次回复」、说明「收到点名邮件后最多迭代三轮再回复」，再粘贴下面的 behavior。无需填写原型 name；创建成功后服务端返回 `p-<uuid4 hex>`，并写入 prototype.tlon。display_name 必填且描述行为，技术 ID 不表达职业或人格。

原型的 trusted、author 与 generation 由创建渠道和谱系决定，不能通过显示名授予职务。手写创建进入 trusted 库，LLM 提案进入 staging，批准不改变其 ID。

### 2.2 behavior.tlon

```edn
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (or (addressed-to-me? msg) (cc-ed? msg))
          :loop {:context {:sections [(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history 20 10) (thread-recap)]}
                 :stages [(think)
                          (critique {:if (sending-mail?)})
                          (act {:if (or (not (sending-mail?)) (not (critique-failed?)))})
                          (revise {:if (critique-failed?)})]
                 :until (or (iteration-limit-reached?) (error-limit-reached?) (token-limit-reached?))
                 :max-iterations 3
                 :max-consecutive-errors 2
                 :max-tokens 200000
                 :reflect {:enabled true :interval-minutes 10}}}
         {:name "boot"
          :on :boot
          :actions []}]}
```

（本示例已用 `tlon.agents` 的解析器验证：合法 EDN + schema 校验通过。）

逐段拆解：

- **`{:version 1}`** 顶层必须。
- **`:rules [...]`** 是规则向量。`:name "respond"` 规则：**`:on :mail-delivered`** 触发（收到投递事件）、**`:when`** 门控（收件人含自己才响应——没有这个门，系统里每一封邮件都会唤醒它）。
- **`:loop`** 使它挂认知引擎：
  - `:context :sections` 注入什么上下文段（`persona`/`protocol`/`lessons`/`skills`/`knowledge`/`current-task`/`mail-history N`（或两段式 `mail-history F R`，ADR 0025）/`thread-recap` 八种，见 dsl-reference §5.1）；段的排列按缓存友好原则——几乎不变的段靠前，每会话必变的段靠后；
  - `:stages` 认知循环的阶段程序。默认形态是 `think` + `act` 加一道交卷前自查：`(critique {:if (sending-mail?)})` 只在即将派发邮件时评审待交回复，FAIL 时 `(act {:if (or (not (sending-mail?)) (not (critique-failed?)))})` 拦截派发、`(revise {:if (critique-failed?)})` 把反馈喂回下轮修正；还可以加 `plan` 等自省积木（见 dsl-reference §5.2）；
  - `:until` 停机条件 + `:max-iterations` 迭代上限 + `:max-tokens` token 预算（ADR 0023：两条引擎硬顶取或、先到先停；DSL 的停机性靠预算，不靠砍循环）；
  - `:reflect` 反思开关，`enabled` 与 `interval-minutes` **都必填**（`ReflectSpec`）。
- **`:on :boot` 规则**：带它的成员开机即启动；不带的按需唤醒（startup 模式由此派生，`agent.yaml` 不再控制）。

### 2.3 人格文件：创建时播种，不进原型

原型**不带人格文件**（统一成员模型，ADR 0006）。成员的人格（`IDENTITY.md` / `SOUL.md` / `RULES.md`）在创建成员时播种：创建时经 WebUI / admin API 的 `persona` 字段写入，之后 `IDENTITY.md` / `SOUL.md` 是**成员自主数据**——成员可经 `updatePersona` 工具全文自改（ADR 0024，留痕可问责），`RULES.md` 仍由人类编辑（成员 `config/` 下直接改，或 ops Agents 视图 `e` 编辑 / WebUI 成员编辑）——人格不进基因库，不随 spawn 遗传。成员无人格文件也安全：`(persona)` section 读到空，prompt 里没有身份段，成员照常运行。

## 3. 从原型派生 agent

`behavior.tlon` 会在派生时**逐字拷贝**进 `~/.tlon/{agent_id}/config/`，此后改原型不影响已派生的 agent（拷贝即冻结）；`agent.yaml` 的 `prototype` 字段记录来源（血缘）。两种派生入口：

### 方法 A：WebUI（人类）

AGENTS → CREATE → 按行为显示名选择原型 → 填成员名字、模型（provider/model）与可选人格种子 → CREATE。服务端分配 `m-<uuid4 hex>` 并返回地址，表单不接受 agent_id。成员的显示名可以表达自己的社会身份；原型只是行为来源。

### 方法 B：spawnAgent（LLM）

认知 agent 自己用 `spawnAgent` 工具，指定 `prototype`、`name`、可选 `params`；子成员继承 spawner 的模型（ADR 0016——成本/能力画像随谱系）。派生出的成员（`m-<uuid4 hex>`）与手建成员同构（统一成员模型，ADR 0006）：永不删除，空闲睡眠、档案归档；是否开机启动由原型 spec 的 `:on :boot` 规则派生。

## 4. 无 `:loop` 的原型

无 `:loop` 的规则只跑 `:actions`、不启动认知会话（统一装配下能力对象仍存在，只是不被使用——对象便宜，调用才贵）。内置的「定址转发」是完整示例（[定址转发行为](../../src/tlon/prototypes/p-39c280594c5b4e4084ce8aad72e6b07f/behavior.tlon)）：

```edn
{:version 1
 :parameters {:to {:type :string-list :required true}}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ${to}})]}
         {:name "boot"
          :on :boot
          :actions []}]}
```

这里演示了 `:parameters` 模板（**写侧专属**）：顶层声明参数槽，规则体用 `${to}` 引用。spawn 时传 `params={"to": [...]}`，落盘前 [agents/behavior_template.py](../../src/tlon/agents/behavior_template.py) 的 `render_template` 把占位符渲染成序列化的 EDN 字面量——运行时永远见不到模板语法。参数类型只有 `:string` 与 `:string-list` 两种；必填缺失、未知参数、类型错误全部拒绝。同一个转发原型由此能派生出转发目标各异的任意多个实例。

## 5. LLM 创作路径（staging 闸）

「agent 创造 agent」不直接落进 trusted 库，走两区制：

1. **创作**：LLM 用 `writePrototype` 工具，传 `display_name` + `behavior`（EDN 字符串）；可选 `parent`（父原型 ID，挂谱系，服务端推导 `generation`）与 `proposal_note`（本代变更说明）。产物写入 `~/.tlon/prototypes-staging/`，元数据强制 `:trusted false`、`:author` 记录作者 id。校验**先于落盘**：模板校验 → EDN 解析 → MemberSpec schema 校验（用无 host 谓词的默认注册表——Adam 的词汇表不许过闸），失败则什么都不写、错误原文返回给 LLM 改了重试。
2. **审批**：人类在 WebUI 的 PROTOTYPES staging 列表批准或拒绝。`approve_prototype` 把目录移入 `~/.tlon/prototypes/` 并改写 `:trusted true`（`:author` 与谱系字段保留，审计链不断）；`reject_prototype` 整个删除。
3. **实例化**：批准后即可被 `spawnAgent` 使用。`spawnAgent` 对 staging 里的不可信原型直接拒绝（`untrusted_prototype`），且在一切预算检查之前——不可信原型连配额都消耗不到。

## 6. 校验与排错

- **EDN 解析**：`behavior.tlon` 必须能被 `tlosure.edn` 的 `loads` 解析。最易踩的坑是 **map 缺闭合 `}`**——每条规则的 map 都得有自己的收口，`:loop` 的 map 闭合后还要再闭合规则 map（内置原型曾集体缺这一层——历史已修复，见 `design/prototypes.md` 已知问题；`edn` 裸解析能立刻暴露，spawn 到启动装配才暴露的是更晚的坑）。写完先本地解析验证（见下）。
- **schema 必填项**：`:loop` 里 `:until`、`:max-iterations`、`:max-consecutive-errors`、`:max-tokens`、`:reflect`（`enabled` + `interval-minutes`）都是必填，缺一个 `validate_member_spec` 直接拒。
- **模板占位符**：带 `${var}` 的文件**不是**合法 EDN（`$` 会让裸解析报错），这是正常现象——模板只在渲染后才是 spec。用 [agents/behavior_template.py](../../src/tlon/agents/behavior_template.py) 的 `analyze` 做结构校验（把占位符替换成 stand-in 后试解析）。
- **门控忘写**：`respond` 规则没有 `:when` 门控的话，系统里每封邮件都会唤醒这个成员。
- **语法对照**：builtin/stage/section 完整清单与常见加载失败行为，见 `reference/dsl-reference.md` §4–§9。

本地验证一份 `behavior.tlon`：

```bash
uv run python -c "
from tlosure.edn import loads
from tlon.agents.member_spec import MemberSpec, validate_member_spec
spec = MemberSpec.from_edn(loads(open('behavior.tlon').read()))
validate_member_spec(spec)   # 无异常即通过
print('OK', any(rule.loop is not None for rule in spec.rules))
"
```

> 路径：`tlosure.edn`（`loads`）与 `tlon.agents.member_spec`（`MemberSpec` / `validate_member_spec`）是 [agents/prototypes/](../../src/tlon/agents/prototypes) 包里实际使用的导入。

## 7. 检查清单

写完一个原型，过一遍：

- [ ] 目录在 `~/.tlon/prototypes/{name}/`，有 `behavior.tlon`（判定标准）；
- [ ] `prototype.tlon` 含 `:name`（缺省兜底目录名）、`:display-name`、`:description`；
- [ ] `behavior.tlon` 是合法 EDN，能 `loads` + `validate_member_spec`；
- [ ] 带 `:loop` 的规则写全了 `:until` + 迭代上限 + `:max-tokens` + `:reflect`；无 `:loop` 的规则纯 `:actions`；
- [ ] 每个 `respond` 规则都写了 `:when` 门控；
- [ ] 参数化原型：`:parameters` 声明与 `${var}` 引用闭合，参数类型只用 `:string` / `:string-list`；
- [ ] LLM 创作走 staging + 人类审批，不直接写 trusted 库。
创建接口会拒绝旧 name／agent_id 参数，没有旧职业原型名别名。技术标识、行为显示名与社会身份的边界见 [ADR 0035](../decisions/0035-generated-ids-and-behavior-prototypes.md)。
