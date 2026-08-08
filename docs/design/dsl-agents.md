# DSL 书写 Agent

> 状态：设计定稿（2026-07-22 讨论收敛）· 阶段 1 已完成（2026-07-22）· 阶段 2 已完成（2026-07-22）· 阶段 5 已完成（2026-07-22）· 阶段 6a 已完成（2026-07-22）· 阶段 6b 已完成（2026-07-23）· 阶段 3a 已完成（2026-07-23）· 阶段 4b 已完成（2026-07-23）· 阶段 7a 已完成（2026-07-31）· 阶段 7b 已完成（2026-07-31）· 阶段 7c 已完成（2026-07-31）· total spec 迁移已完成（2026-07-31）· P5a ScriptMember 骨架已完成（2026-08-05）· P5b relay + spawn 路径 runtime 感知已完成（2026-08-05）—— 1–6 收官，7a 起循环进入解释执行，7b 起上下文构建进入表达式化，7c 起 stage 家族扩展为 think/act/plan/critique/revise 并支持 stage 条件；total 迁移后所有 agent 由完整 behavior 文件描述，无 DefaultSpec、无 partial merge；P5a 起 script 成员策略即数据（亚当为第一个配置实例），P5b 起 script 成员可经 prototype/spawn 创建（relay 落地，`:llm false` 逃生门以 script member 形态兑现）
>
> 关联：本设计是审计报告 P1 （拆分 agent.py）的目标架构；路线图阶段 1 即 。

## 核心理念

**Agent 的行为是数据，不是代码。**

当前 `agent.py` 中硬编码的四块行为——何时触发、如何构建上下文、跑什么循环、完成后做什么——全部外部化为一份可序列化的**行为规约（AgentSpec）**。Tlon server 从"运行写死的 agent 逻辑"变成"解释执行行为规约的引擎"：

- 规约存储在文件中，一份规约的解释执行即一个 agent 的运行；
- 规约可被 LLM 生成、被 agent 改写——**环形废墟：agent 创造 agent**；
- 一份规约可以触发另一份规约的解释执行（`spawn`），即 agent 触发 agent。

## 设计决策（已收敛）

### 1. 先语义，后语法

`AgentSpec` 是类型化 Python dataclass，是语义的**唯一权威**；字段全部为纯数据，保证可序列化。任何 DSL 文本格式都只是它的序列化前端，编译（解析 + 校验 + 求值）到 AgentSpec。

### 2. 循环由引擎执行，DSL 只描述

铁律：**DSL 永不执行迭代**。认知循环（think→act→observe）属于运行时，因为 Tlon 的步进/收割循环结构是 asyncio 单线程协作调度的直接产物，循环形状与并发模型纠缠，必须由 Python 引擎掌控。

- **DSL 不提供 `while`，也不提供递归**。递归是后门版的无限迭代：求值器一旦有 `if` + 递归 + 数据结构即图灵完备，保证停机与静态分析随之坍塌（先例：Bazel Starlark 刻意禁止递归）。
- v1：循环模式为枚举——`react` / `plan-execute` / `reflexion` / `cron-triggered`。
- v2：开放 **stage 机**——循环是一组类型化 stage 的编排数据，DSL 获得"组合新循环形状"的自由，而非"发明迭代原语"的自由：

```clojure
{:loop {:stages [(plan {:max-steps 3})
                 (act {:parallel true})
                 (critique {:llm "self"})
                 (revise {:if (critique-failed?)})]
        :until (or (no-more-tools?) (max-iterations 10))}}
```

每个 stage 是 Python 实现、可测试的语义单元。（阶段 7a 已以受限形式落地：白名单 stage 向量 + until 表达式，见路线图；阶段 7c 已落地 sketch 的主体形态——`(plan {:max-steps N})` / `(critique)` / `(revise {:if ...})` 三个新 stage 与 per-stage `:if` 条件。与 sketch 的差异：`act` 无 `:parallel`（并发模型属引擎，不进 DSL）、`critique` 无 `:llm` 参数（评审就用本 agent 的 LLM，无需旋钮）、`:until` 谓词集合是 `iteration-limit-reached?` / `error-limit-reached?` 而非 sketch 的 `(no-more-tools?)` / `(max-iterations 10)`。）

### 3. 图灵不完备（刻意）

Agent 的智能在 LLM 里，能力在工具里；DSL 是胶水层，决定*何时*、*以什么形状*调用 LLM 和工具。胶水层的计算能力不让 agent 更强——任意计算的出口是 `call-tool`（Docker 沙箱），被容器隔离。

图灵不完备买到的关键能力用在 Tlon 最要的环节：**agent 改写自己的行为文件**。保证停机、可静态分析、可 fuel 计量的规约，解释器能在激活前完整验证它。自我修改越强，越需要最强的静态保证。

语言内真需要计算时（如"对记忆打分排序"），答案是**加内置函数**（Python 实现、可测试），不是放开语言。语言核心保持小，builtin 集合按需增长。

### 4. 单一 Lisp 语法

不做 YAML 前端，人类和 LLM 写同一种 s-expr。理由：

- 狗粮原则：语法若人类写着难受，则设计失败，LLM 也不该用；
- 避免双前端的解析器/文档/语义漂移成本；
- 先例：Guix 用 Guile Scheme 配置整个操作系统；
- EDN 风格字面量对嵌套结构的可读性优于 YAML，且 YAML 缩进对人和 LLM 一视同仁地坑。

自研迷你 Lisp（EDN 字面量 + 白名单特殊形式），纯 Python 实现（约 300–500 行），**不引入 JVM/Clojure/Hy**（JVM 运维重量；Hy 可 import 任意 Python 模块，无法沙箱）。

### 5. spawn 互触发

规约可声明 `on-complete` 时触发另一份规约文件的解释执行。必须有**全局深度/速率限制**防消息风暴。

### 6. spec 是 total 的（2026-07-31 拍板，破坏性）

**behavior 文件即 agent 行为的全部**：四节（`:trigger` / `:context` / `:loop` / `:on-complete`）与节内必填字段一个都不能少。文件缺失、解析失败、不完整——**拒绝启动该 agent**（registry 记 `agent_start_failed`，其余 agent 与服务端不受影响）。

- 无 DefaultSpec 派生、无 base、无 partial merge、无 spawn overrides——加载路径上不存在任何"缺省继承"；
- prototype 同样是完整文件，实例化是纯拷贝（overrides 与 `_deep_merge` 已删除）；
- `AgentConfig.max_iterations` / `max_consecutive_errors` 与 memory config 的 reflect 字段已删除，行为权威只在 behavior 文件；
- `:loop` 的 `:mode` / `:tools` 死字段随 7a 的 stages 一并移除；
- 破坏性变更，无历史兼容（项目未发布）。早期阶段达成情况中关于 DefaultSpec / partial merge / overrides 的描述均以此节为准。

`standard_behavior_edn()`（`agents/spec.py`）导出 canonical 完整文件文本，wizard 创建 agent 时落盘——"完整"由工具生成，不由人手写保证。

## 语言设计（分层落地）

**L0 — EDN 数据字面量（已落地，`agents/edn.py`）**：map / vector / keyword / string / number / bool。v1 规约文件是纯数据，只解析不求值；trigger/loop/context 全部为声明式数据。

v1 落地的真实 schema（`~/.tlon/{agent_id}/config/behavior.tlon`，实现于 `agents/spec.py`）：

```clojure
{:version 1                        ; 必填，只接受 1
 :trigger {:on-mention true}      ; 必填；To/CC 含我的地址即响应。另可选 :when 表达式
 :context {:sections [(persona)        ; 必填（7b）；section 向量，按序拼装
                      (protocol)       ;   persona/protocol 喂 system prompt
                      (knowledge)      ;   knowledge/mail-history 按序喂对话上下文
                      (mail-history 20)]}  ; 邮箱历史条数（inbox+sent），0=关闭
 :loop {:stages [(think) (act)]    ; 必填（7a）；stage 向量，此为 react 标准程序
        :until (or (iteration-limit-reached?)   ; 必填（7a）；终止表达式
                   (error-limit-reached?))
        :max-iterations 10         ; 必填；引擎硬顶，until 无法越过
        :max-consecutive-errors 3} ; 必填
 :on-complete {:save-interaction true              ; 必填；会话完成后写入记忆
               :reflect {:enabled true             ; 必填；反思开关
                         :interval-minutes 15}}}   ; 必填
```

规则（total 语义，决策 6）：四节与节内必填字段缺一不可，缺失/不完整/解析失败 → 拒绝启动；无 base、无 partial merge、无旧键展开（7b 的 `:persona` 等旧键已随迁移成为未知键，warning 忽略）；类型错误报带字段路径的清晰错误。keyword / symbol / string 是三种不同类型。`:stages` 每项形如 `(name)` 或 `(name {opts})`，name 白名单 `{think, act, plan, critique, revise}`（7c）；`:until` 加载时经 `LOOP_BUILTINS` 纯谓词注册表静态校验，不执行。`:sections` 每项形如 `(persona)` / `(protocol)` / `(knowledge)` / `(mail-history N)`（N 为非负 int，恰好一个参数），name 白名单、不可重复；当前邮件永远由引擎追加在最后，不在 DSL 内。

**L1 — 受限求值器（阶段 3a 已落地 trigger 表达式，`agents/eval.py`）**：

- 特殊形式白名单：`if` / `when` / `let` / `and` / `or`，仅此而已（`match` 留待后续）；无 `loop`/`recur`、无 `def`、无宏；
- 领域内置谓词与动作：`addressed-to-me?` / `cc-ed?` / `content-matches?` / `contains?` / `=` / `not`（纯谓词）；`spawn` / `emit` 动作 4b 已落地（on-complete 专用，求值纯、引擎执行）；`call-tool` / `score-memories` 等留待后续（~~`sender-type`~~ 已于 2026-07 移除；~~`mentioned?`~~ 随 R1b 邮件模型退役——"提及"不再是路由依据，proactive 整套同期删除，见 [mail.md](./mail.md)）；
- **fuel 限制**：求值带步数上限（默认 1000）与深度上限（默认 100）；
- 数据化 schema 校验（malli 风格，校验器本身也是数据）。

3a 实测语法（trigger 表达式已可用，`:trigger` 节接受 map 或表达式两形态）：

```clojure
;; reviewer.tlon —— 一份 agent 行为规约（total：四节齐全，字段完整）
{:version 1
 :trigger (and (addressed-to-me? msg)              ; To 含我的地址
               (content-matches? msg "？$"))       ; 且内容以问号结尾
 :context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
 :loop {:stages [(think) (act)]
        :until (or (iteration-limit-reached?) (error-limit-reached?))
        :max-iterations 10 :max-consecutive-errors 3}
 :on-complete {:save-interaction true :reflect {:enabled true :interval-minutes 15}}}

;; 其余特殊形式：if / when / let（顺序绑定）/ and / or（短路）
;; (when (cc-ed? msg) (not (content-matches? msg "^(忽略|skip)")))
;; on-complete 动作表达式（4b 已落地）：
;; :on-complete {:actions [(when (content-matches? result "完成")
;;                          (spawn "archivist" {:name "归档员"}))
;;                        (emit "本轮处理完毕" [])]}
```

## 安全闸（贯穿始终）

- 规约经数据化 schema 校验才能激活；
- LLM 生成的规约默认不授予 `shell` 等高危工具，需用户显式批准；
- 新规约先 dry-run（trace 回放）再激活；
- spawn 链有全局深度/速率限制。

## 可观测性要求

LLM 写的行为必然出 bug，可观测性是必需品：

- 每次解释执行在 trace 中记录**生效规约的版本哈希**；
- 日志记录 DSL 决策点（trigger 命中/未命中及原因）；
- 每个 DSL 解释步骤对应一个 span。

## Behavior Prototype 库（✅ 已实现，2026-07-22）

预制的行为原型，让人类和 agent 都能以"模板 + 覆盖"的方式创建新 agent。

**Prototype = AgentSpec + 人格文件模板 + 工具白名单**，目录形式：

```
prototypes/
├── deep-thinker/          # 深度思考者（:max-iterations 大、显式 stage 程序：think/act/critique + 条件 revise，阶段 7c）
│   ├── behavior.tlon
│   ├── IDENTITY.md / SOUL.md / RULES.md
├── fast-responder/        # 快速反应者（迭代少、context 精简）
├── relay/                 # 消息转发者（script runtime：script.tlon + :runtime :script，无 LLM，P5b 落地）
└── archivist/             # 档案管理者（reflect 频繁、记忆工具全开）
```

设计决策：

- ~~**继承机制现成**~~（total 迁移后废止）：实例文件必须是完整文件，prototype 不再充当 merge base——继承关系只剩"拷贝时刻的来源记录"（`AgentConfig.prototype`）；
- **拷贝而非引用**：实例化时把 prototype 内容复制为实例自有文件，行为冻结在创建时刻——Tlon 升级改了 prototype，不影响存量 agent；
- **存放**：包内预制，首次运行拷贝到 `~/.tlon/prototypes/`（与 `default_team.py` 种子机制同构），用户可改可加；
- **发现**：人类走 `tlon mirror` 创建菜单；agent 走 `list_prototypes` 工具；
- **已知缺口（已关闭，P5b）**：纯转发者不需要 LLM——原登记的"`:llm false` 逃生门"未以 behavior schema 扩展落地，而是以 **script member** 形态兑现：relay 是 `:runtime :script` 的 prototype（目录含 `script.tlon` 而非 `behavior.tlon`，无人格三件套），实例化为 `AgentConfig.runtime="script"` 的成员，registry 装配 ScriptMember 而非 ReAct Agent（见阶段 P5b）；
- **显式程序狗粮**（阶段 7c）：deep-thinker 的 `:loop` 从"只调 `:max-iterations` 旋钮"改为显式 stage 程序 `[(think) (act) (critique) (revise {:if (critique-failed?)})]`——每轮 think/act 后评审最新输出，FAIL 时把反馈喂回上下文让下一轮修正；fast-responder / archivist 保持不动（默认程序即 react），验证新旧形态在同一引擎上共存。

## 动态 Agent Registry（6a 休眠/唤醒、6b spawn/stop 已实现）

Scale-to-zero 的多 agent 系统：无沟通时 0 个运行中的 agent，消息到来按需激活。

### 核心概念：身份与运行时分离

- **Persona（持久）**：`~/.tlon/{id}/` 下的人格文件、记忆、配置，永远存在；
- **Activation（瞬态）**：主循环、Docker 容器、内存中的 session，按需激活、闲置回收。

类比 Orleans virtual actor：身份永恒，activation 随消息唤醒、闲置钝化。Registry 管理 activation，persona 目录是全集。

### 两种身份（已拍板）

- **居民（resident）**：有名有姓的持久 persona（现有 6 人 team 即居民）。可休眠，唤醒后还是那个有记忆的个体。"0 运行 agent"是资源配置（所有居民设为可休眠），不是架构强制；
- **临时工（contractor）**：agent 用工具从 prototype 现造，完工即弃，persona 不保留。是否可"转正"为居民——开放问题，实现时再定。

**孤儿策略（6b 拍板：reparent，不连坐）**：spawner 休眠/停止后，它造的临时工**继续独立运行**（类比上相当于挂到 init 下），由各自的 idle timeout 回收——临时工一旦被 mention 就可能与其他人建立会话，连坐杀会误伤无辜对话方。`spawned_by` 是审计记录，不是生命周期锁。

### 唤醒与路由（wake-on-mention）

- Router 查 **persona 全集**（居民目录 + 运行中的临时工），不再只看运行中的 agent；提到沉睡 persona → 先激活再投递；
- 唤醒竞态：同一人被并发 @，registry 加锁只激活一次；
- 提到不存在的名字 → 错误提示或转 receptionist。

### 冷启动对策

- 容器懒启动：只有用到 shell 类工具才起 Docker 容器（纯对话不需要）；
- 温池：保留 0~1 个 receptionist 常驻（顾言蹊式 proactive 协调者），先接住消息、转述、唤醒专家；
- 唤醒期间 router 先发系统消息"正在唤醒 X…"，客户端体验自然。

### 回收策略

- 显式 `stopAgent` 工具调用，或空闲超时（临时工默认 300 秒无新消息）；
- **必须 drain**：存在 ACTIVE/WAITING session 时不许停，等 session 完结再回收；
- **完工即弃（6b）**：contractor 被显式停止或被空闲回收时**删除其 home 目录**（`~/.tlon/{id}/`，删除前记日志，且严格限定 role==contractor + 路径在 tlon_home 下）；resident 回收只停进程不删目录；
- 休眠不反思：reflection 是定时烧 token 的，睡着不反思；醒来时如有未巩固交互再补（`min_interactions` 闸门已有雏形）。

### spawn 预算（防组合爆炸，6b 默认值）

- 全局并发 activation 上限：`max_activations = 8`（含 resident）；
- spawn 深度上限：`max_spawn_depth = 3`（resident depth=0，contractor = spawner.depth + 1）；
- 每会话配额：`session_spawn_quota = 2`——同一 (spawner, session) 内 spawn 数上限；session_id 经 structlog contextvars 从 `_session_span` 传入，读不到（如测试直调）时同一 spawner 的裸调用共用一个桶；
- 工具权限不可升格：只允许从 prototype 库 spawn（Tlon 预制 prototype 为可信源；LLM 现写的规约仍走人工批准闸，是阶段 4 的事），行为文件逐字拷贝（total 迁移后 overrides 已删除），tools 不在 spec schema 内故天然无法升格。

### 附：操作系统类比（灵感，非规约）

> **注意**：本节只是类比/灵感，用于辅助理解与启发设计，不构成架构规约。词汇不进代码命名（用户可见层继续用博尔赫斯词汇），机制以本文其他章节的正式设计为准。

Tlon 的 prototype/agent 体系与操作系统的"可执行文件/进程"高度同构：

| Tlon | 操作系统 |
|------|---------|
| Prototype（behavior.tlon + 人格 + 工具白名单） | 可执行文件（磁盘上的程序） |
| Activation（主循环 + 容器 + session） | 进程 |
| AgentRegistry | init/pid 1 + 进程调度器 |
| Wake-on-mention | systemd socket activation / inetd |
| 空闲 drain 休眠 | cgroup freezer / swap out |
| spawn 预算 | `RLIMIT_NPROC` / cgroup |
| 工具白名单 / 权限不可升格 | capabilities(7) / 禁止 setuid |
| EventBus | IPC / dbus |
| REST `/agents` | `ps` / `/proc` |
| 每 agent 一个 Docker 容器 | 每进程独立 namespace/jail |

类比带来的设计灵感：

- **信任分级 ≈ 代码签名**：Tlon 预制 prototype = 签名二进制（可信，直接执行）；LLM 现写规约 = 未签名二进制（Gatekeeper 模型：校验 + 用户显式批准）；dry-run = 沙箱试运行；
- **目录布局可参照 FHS**：包内 prototypes ≈ `/usr/bin`（只读、随发行版升级），`~/.tlon/prototypes/` ≈ `/usr/local/bin`（用户安装），agent home ≈ `$HOME`；
- **6b 可参照进程模型**：spawn/stop ≈ fork/exec + kill，spawner 与临时工是父子关系——进程模型提示必须回答"父进程死了，子进程 reparent 还是一起杀"（spawner 休眠后它造的临时工何去何从）。

类比失效处（同样要记住）：进程死后一无所有，而 agent 醒来带着完整记忆（更像 CRIU 检查点恢复 + 用户账号：persona 是持久数据，activation 只是登录会话）；进程等待输入，agent 有自主循环（更像 daemon + cron）。

## 路线图

### 阶段 1 — 拆分 agent.py（P1 ）✅ 已完成（2026-07-22）

拆为：`Agent` 薄壳（编排）+ `SessionScheduler`（主循环）+ `LoopDriver`（react 步进/收割）+ `TriggerEvaluator` + `ContextBuilder`（独立 `context_builder.py`，与纯函数式 `SystemPromptBuilder` 分工）+ `ReflectionEngine`。顺手补：session 最大轮数/空闲超时、消息队列背压、`error_count` 消费。

完成标准：行为不变，测试全绿 + 真实冒烟通过。**达成情况**：261 个测试全绿（含 6 个新增韧性测试），ruff / 架构检查 / re-export 检查全过；真实冒烟未做（留待阶段 2 前人工验证）。

### 阶段 2 — AgentSpec + EDN 数据文件 ✅ 已完成（2026-07-22）

定义 `AgentSpec` dataclass（纯数据字段）；写 `DefaultSpec` 精确复刻当前行为；Agent 改为"读 spec 驱动组件"的解释器；规约文件为纯 EDN 数据字面量（只解析不求值）。

完成标准：现有 agent 配置等价迁移，行为不变，测试全绿。**达成情况**：311 个测试全绿（261 既有 + 50 新增：edn 32、spec 18），ruff / 架构检查 / re-export 检查全过。落地偏差：现状 no-tool reminder 无开关，故 v1 schema 不含 `:no-tool-reminder` 字段（reminder 保持无条件触发）；`AgentConfig` 的 proactive/max_iterations/max_consecutive_errors 保留为 DefaultSpec 数据源与路由预筛来源，`session_timeout_seconds`/`message_queue_size` 作为构造期 infra 不进 spec。

### 阶段 4a — LLM 写数据规约 + 信任闸 ✅ 已完成（2026-07-23）

> 路线图于 2026-07-23 重排：原阶段 3（求值器）与阶段 4（LLM 生成）按价值密度重切为 4a / 3a / 4b；stage 机循环（原 3b）放弃，移入"明确不做"。

agent 获得 `writePrototype` 工具：写纯数据规约（v1 schema behavior.tlon）+ 人格文件到 staging 区（`~/.tlon/prototypes-staging/`）；**信任分级落地"代码签名"模型**——包内/用户 prototype 可信（`:trusted true`，可直接 spawn），LLM 写的进 staging（不可信，`spawnAgent` 拒绝），人类在 mirror 批准后转正；trace 记录生效规约的版本哈希。

完成标准：用户对话让 agent 写一份规约 → mirror 批准 → spawn 运行，端到端走通；规约版本哈希出现在 trace 中。

**对原阶段 4 的修正**：dry-run（trace 回放）砍掉——语义不清、实现重、收益虚；静态校验 + spawn 预算 + `stopAgent` 随时可杀已构成足够安全网。

**4a 达成情况**：423 个测试全绿（386 既有 + 37 新增信任闸测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- **信任模型**：`prototype.edn` 新增 `:trusted`（包内 3 个 builtin 补 `true`）；`PrototypeInfo` 加 `trusted`/`author`（缺 `:trusted` 的用户手写 prototype 默认可信——手写即信任，只有 staging 区写 false）；`spawn_contractor` 在预算检查前加信任闸，不可信抛 `SpawnRejectedError(reason="untrusted_prototype")`，`spawnAgent` 翻译为可读错误；`listPrototypes` 输出带 `trusted`；
- **staging 区**：`~/.tlon/prototypes-staging/`（paths 无副作用解析）；`write_staging_prototype()` 先校验后落盘（EDN 解析 + AgentSpec schema：`:version` 必填、类型、`:loop {:mode}` 限已实现值），失败不写盘；name 字符集 + 双向查重；metadata 写 `:trusted false` + `:author`；`approve_prototype()` 移目录转正（author 保留）、`reject_prototype()` 删目录（防护同 contractor home 删除）；
- **`writePrototype` 工具**（工具总数 21 → 22）：name/behavior 必填 + display_name/description/identity/soul/rules；成功返回 `pending_approval`，校验失败返回具体错误 + schema 提示；
- **mirror 审批入口**：wizard 人员管理菜单"规约审批"——列 staging（含作者）→ 预览 behavior.tlon 与人格文件 → 批准/拒绝；
- **spec 版本哈希**：`AgentSpec.source_hash`（behavior.tlon 内容 sha256 前 12 位；DefaultSpec 为 `"default"`），落进 `agent.session` span attributes（`spec_hash`）、`agent_started` 日志与 `AGENT_STARTED` 事件 payload。

### 阶段 3a — trigger 表达式 ✅ 已完成（2026-07-23）

受限求值器的窄落地，只做条件判断：白名单特殊形式（`if/when/let/and/or`）+ **纯谓词** builtin（`mentioned?` / `content-matches?` / `addressed-to-me?` 等），fuel 限制。**builtin 分两类**：纯谓词（可用于 trigger，无副作用）与动作（`spawn`/`emit`，只能出现在 on-complete）——条件判断绝不允许副作用。

完成标准：trigger 可用表达式书写（如只响应"直接 @ 我且内容含问题"），与纯数据写法结果一致；fuel 超限安全报错。

**3a 达成情况**：475 个测试全绿（423 既有 + 52 新增求值器/表达式测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- **EDN list 支持**：`edn.py` 新增 `Form(list)` 类型——`(...)` 解析为 Form（代码），`[...]` 仍为 list（数据），两者类型可区分；嵌套、`dumps` roundtrip 保类型；纯数据语境语义不变；
- **受限求值器 `agents/eval.py`**：`eval_form(form, env, builtins, fuel=1000, max_depth=100)`。字面量/keyword 自求值，vector 逐项求值，Symbol 查 env；特殊形式仅 `if`（2–3 参）/`when`/`let`（vector 顺序绑定）/`and`/`or`（短路）——无 `def`/`lambda`/`loop`/`recur`/`match`，递归在语言层面无从表达。每步求值耗 1 fuel，耗尽抛 `FuelExhaustedError`；深度超限抛 `EvalDepthError`；错误尽量带形式本身（`dumps`）。`validate_form` 静态检查：形式头白名单 + Symbol 作用域（`msg`/`self` + `let` 绑定）+ `let` 形状，不执行；
- **builtin 纯度机制**：`Builtin(fn, pure=True)` 注册表；`TRIGGER_BUILTINS` 只注册 6 个纯谓词/函数（`addressed-to-me?` / `cc-ed?` / `content-matches?` / `contains?` / `=` / `not`），`spawn`/`emit` 不注册——trigger 求值无副作用（by construction）。R1b 邮件语义：`addressed-to-me?` = To 含我的地址，`cc-ed?` = CC 含我的地址（`mentioned?` 已退役，引用它的旧表达式在 spec 加载时校验失败并回退 DefaultSpec）。env 的 `msg` 是裸 Mail 但表达式无属性/方法访问语法，只能经 builtin 检视；
- **TriggerSpec 两形态**：`:trigger` 接受 map（数据模式，不变）或 Form（表达式模式）；`TriggerSpec.when: Form | None`。`from_edn` load 时静态校验表达式（未白名单形式/函数、未定义 Symbol 直接 `SpecValidationError`），`writePrototype` schema 校验自动覆盖；`to_dict`/`from_dict` roundtrip 保留 `when`（EDN 字符串形态）；
- **TriggerEvaluator 接线**：自己消息跳过（不变）→ `when` 非空走表达式（env = `msg`/`self`，真值即响应；`EvalError` 安全默认不响应 + `trigger_eval_failed` warning）→ 否则数据逻辑不变；表达式命中/未命中记 `trigger_expr_result` debug；`when` 与数据字段并存时以 `when` 为准。

### 阶段 4b — on-complete 动作表达式 ✅ 已完成（2026-07-23）

依附 3a 的求值器：`on-complete` 支持 `(when ...)` 条件与 `spawn`/`emit` 动作表达式（原阶段 4 spawn 互触发的剩余部分）。

完成标准：`:on-complete {:actions [...]}` 可写动作表达式；求值保持纯（动作变数据，引擎执行）；spawn 走信任闸与预算；单动作失败不影响 session。

**4b 达成情况**：505 个测试全绿（475 既有 + 30 新增 on-complete 测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- **求值纯、动作变数据、引擎执行**：`spawn` / `emit` 注册为 `Builtin(pure=False)`，但求值时不做任何副作用——校验参数后把 `ActionRequest` 收集进 collector（`make_actions_builtins(collector)`；模块级 `ACTIONS_BUILTINS` 为无 collector 的校验用注册表）；求值完成后由 Agent 异步逐条执行。延续"DSL 只描述，引擎执行"铁律，求值器保持同步、可静态校验；
- **schema**：`OnCompleteSpec.actions: tuple[Form, ...]`（纯数据，frozen 兼容）；`:actions` 必须是 vector of Form，load 时每个 Form 经 `validate_action_form` 静态校验——**两位置纯度**：`when` 条件位只许纯谓词（`TRIGGER_BUILTINS`，条件位出现动作直接拒绝），动作位必须是动作 Form 且参数为纯表达式；顶层只许动作 Form 或单层 `when`（不支持嵌套）；`to_dict`/`from_dict` roundtrip 保留（EDN 字符串形态）；
- **动作语义**：`(spawn "prototype" {:name "显示名"})`（第二参数可选，`:name` 缺省自动生成）执行时调 `registry.spawn_contractor`——信任闸与预算全部生效（untrusted/超预算 → 拒绝记日志，不炸 session）；spawner_id=当前 agent，session_id 经 structlog contextvars 从 `_session_span` 读取（完成时仍在 span 内，配额自然归因）。`(emit "内容" ["地址" ...])`（收件人可选，缺省回复触发邮件的发件人）调 `agent._send_mail`，回复头（in_reply_to/thread_id）继承触发邮件。env：`msg`（触发邮件 Mail，经 `session.trigger_message` 传递）/ `result`（本轮最终回复字符串）/ `self`；`content-matches?` 扩展为同时接受 Mail 或字符串；
- **执行点与隔离**：session 以 `is_complete` 收尾时，`_save_interaction_to_memory` 之后、`mark_completed()` 之前（LoopDriver 收尾处）由新子系统 `OnCompleteRunner`（`agents/on_complete.py`）执行一次；单动作失败记 `on_complete_action_failed` error 并继续后续动作，求值失败记 `on_complete_eval_failed`，绝不影响 session 生命周期；观测 `on_complete_actions` info（动作数/类型）。Agent 新增可选构造参数 `registry`（`_start_agent` 注入自身；测试直建为 None 时 spawn 动作记日志跳过）；
- **链式风险**：spawn 出的 contractor 自己的 on-complete 可能再 spawn——由 `max_spawn_depth=3` 硬顶（`OnCompleteRunner` docstring 注明）。

### 阶段 5 — Behavior Prototype 库 ✅ 已完成（2026-07-22）

包内预制 prototype（spec + 人格模板 + 工具白名单），首启拷贝至 `~/.tlon/prototypes/`；实例化走"拷贝 + 部分覆盖"；`tlon mirror` 菜单与 `listPrototypes` 工具两条发现路径。依赖阶段 2 的 base 覆盖语义（已完成）。

完成标准：从 `deep-thinker` prototype 创建新 agent，行为继承 + 覆盖生效，测试全绿。**达成情况**：331 个测试全绿（311 既有 + 20 新增 prototype 测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- 包内 `tlon/prototypes/` 首批 3 个 prototype（deep-thinker / fast-responder / archivist），每个含 `behavior.tlon`（只写与 DefaultSpec 的差异字段）+ 人格三件套 + `prototype.edn` 元数据（自家 edn.py 解析）；hatchling 默认将包数据打入 wheel，已构建验证；
- `agents/prototypes.py`：`seed_prototypes()`（幂等，不覆盖用户修改）、`list_prototypes()`（读 `~/.tlon/prototypes/`，用户增改可见）、`instantiate()`（人格文件拷贝 + behavior 在 EDN 数据层深合并 overrides 后落盘 + 走标准 `_yaml_save` 写 agent.yaml；`AgentConfig.prototype` 记录来源）；
- ServerApp 初始化早段种子；wizard `agent_menu` 加"从 Prototype 创建"入口；`listPrototypes` 工具注册进每个 agent（工具总数 17 → 18）；
- ~~实例化不做 registry 热加载，重启 dream 后生效~~ **已升级（P3 热生效）**：wizard/mirror 创建或实例化后 best-effort 通知运行中的服务端（`POST /admin/config/agents/{id}/reload` → `AgentRegistry.adopt_agent`：reload 配置 → register → 非 lazy 立即启动），新 agent 无需重启即可用；服务端未运行时静默跳过，重启后生效作为兜底；
- **relay 推迟（已落地，P5b）**：纯转发者不需要 LLM，当时推迟等待 `:llm false` 逃生门（见上文"已知缺口"）；逃生门最终以 script member 形态兑现，relay 于阶段 P5b 作为第一个 script prototype 落地。

### 阶段 6 — 动态 Agent Registry

分两步：

- **6a 休眠/唤醒** ✅ 已完成（2026-07-22）：persona 与 activation 分离；router wake-on-mention（查 persona 全集）；容器懒启动；空闲 drain 回收；休眠不反思。完成后即实现"无沟通时 0 个运行中的 agent"；
- **6b spawn/stop 工具化** ✅ 已完成（2026-07-23）：`spawnAgent` / `stopAgent` / `listAgents` 工具 + spawn 预算（全局上限/深度/配额/权限不升格）。与阶段 4 的 `spawn` 合并落地——规约互触发即"触发某 persona/prototype 的一次解释执行"。

完成标准：人类 @ 沉睡居民可唤醒响应；agent 可从 prototype 造临时工、mention 之、停止之，全程受预算约束。

**6a 达成情况**：361 个测试全绿（341 既有 + 20 新增动态 registry 测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- **配置**：`AgentConfig.activation: Literal["always", "lazy"] = "always"` + `idle_timeout_seconds: int = 600`。默认全 always → 行为与之前完全一致（全量启动、永不休眠）；wizard 编辑菜单加"激活模式"项（与 proactive"主动响应"并列，文案区分）；
- **启动分流**：`registry.initialize()` 只启动 always agent；lazy 注册不启动，全 lazy 时 boot 后 0 运行。`get_agent_status` 对 lazy 未运行返回 `"sleeping"`（waking 中返回 `"waking"`，always 未运行保持 `"unknown"`）；
- **唤醒路由**：`RoutedMessage` 新增 `mention_target_ids` 字段（router 在路由决策时把 mention 解析出的 target 子集单独标记）；registry 订阅 `MESSAGE_ROUTED`，只对 mention target 唤醒沉睡 agent——**proactive 广播不唤醒**（决策：否则每条普通消息都会唤起全部 lazy agent，scale-to-zero 失效）；
- **唤醒执行**：`_wake_and_redeliver` 用 per-agent asyncio.Lock 防竞态（并发 @ 同一人只装配一次，但两个事件都会重投递——后到的事件到达时 agent 尚未运行，不投会丢）；复用 `_start_agent` 装配后**重发原事件对象**（同 payload 同 trace carrier），新 agent 的 handler 自然拾取；防循环靠"唤醒后已在运行表"。观测：`agent_waking` / `agent_woken`（含 wake_duration）/ `agent_wake_failed` 日志 + 对应 `AGENT_WAKING/WOKEN/WAKE_FAILED` 事件，唤醒耗时作为 `agent.wake` span 挂在原消息 trace 树下；
- **空闲回收**：registry 起 30s 周期 monitor，lazy agent 空闲超 `idle_timeout_seconds` 且无存活 session → drain。**drain 语义**：有 ACTIVE/WAITING session 绝不停；条件在 per-agent 锁下 shutdown 前再查一次（竞态窗口内有新 session 则放弃）；休眠即移出运行表保留注册，`agent_sleeping` 日志 + `AGENT_SLEEPING` 事件。Agent 暴露只读 `active_session_count` / `idle_seconds`（scheduler 提供："活动"= session 创建 / 存活 session 的 last_active_at / session 完结，无 session 时从主循环启动算起）。休眠不反思：agent 停了主循环自然停，reflection 随之停；
- **容器懒启动**：`_start_agent` 不再 `ensure_running()`；DockerRuntime 的 execute/read_file/write_file/file_exists 本就有"container 为空则先 ensure_running"的懒启动路径，工具首次使用时自动起容器，daemon 不可用表现为工具报错而非 agent 启动失败（`docker_unavailable` warning 语义保留给构造失败）。纯对话唤醒零容器成本；
- **6b 未做项（彼时）**：receptionist 温池、"正在唤醒 X…"系统消息（冷启动体验）留待后续。

**6b 达成情况**：386 个测试全绿（361 既有 + 25 新增 spawn 测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- **身份模型**：`AgentConfig` 新增 `role: Literal["resident", "contractor"]`、`spawned_by`、`spawn_depth`（默认 resident/None/0，存量配置零迁移）；contractor = `activation="lazy"` + `idle_timeout_seconds=300`（registry 构造参数 `contractor_idle_timeout_seconds` 可配）；
- **registry API**：`spawn_contractor(prototype, name, spawner_id, session_id, overrides)`（预算检查 → 生成 id `c-{prototype}-{8位uuid}` → `instantiate()` 写盘 → 补 contractor 字段重存 → `reload_agent` 使配置对 provider 可见 → `register` + `_start_agent` 立即启动 → `AGENT_SPAWNED` 事件）与 `stop_contractor(agent_id, stopped_by)`（role 校验 + drain + shutdown + 注销 + 删 home + `AGENT_STOPPED`）；拒绝抛 `SpawnRejectedError` / `StopRejectedError`（`agents/spawn_errors.py` 独立模块，避免 registry↔tools 循环导入），`.reason` 机器可读；
- **预算默认值**：`max_activations=8`（运行中总数，含 resident）、`max_spawn_depth=3`、`session_spawn_quota=2`（同一 (spawner, session)）；账本在 registry 内存（重启清零可接受）；session_id 由 `Agent._session_span` 经 structlog contextvars 绑定，工具执行（后台 task 继承 context）可读，读不到时同一 spawner 共用单一桶；拒绝记 `spawn_rejected` 日志；
- **孤儿策略 reparent**：spawner 休眠/停止后 contractor 继续独立运行，由各自 idle timeout 回收（见上文"两种身份"）；
- **完工即弃**：contractor 显式停止或空闲回收（`_reap_if_idle` 分支）时删除 `~/.tlon/{id}/`，删前记 `contractor_home_deleted` 日志；删除严格限定 role==contractor，且 id 过字符集校验 + 解析后路径必须在 tlon_home 直下（防路径注入）；resident 永不删目录；
- **三个工具**（`tools/agents/`，camelCase，工具总数 18 → 21）：`spawnAgent(prototype, name, overrides?)`（overrides 的 JSON 字符串键递归转 EDN keyword 后合并）、`stopAgent(agent_id)`（drain 拒绝/resident 拒绝均有明确错误）、`listAgents()`（`get_agent_status` 扩展 role/prototype/spawned_by/spawn_depth 字段）；构造时注入 registry 引用与 spawner 自身 id；
- **ConfigService 顺手修复**：`reload_agent` 原读全局 `paths` 单例，改为按自身 `tlon_home` 解析（隔离 home 下热加载此前是坏的）；`AgentInfoProvider` 增加可选写能力 `reload_agent` / `delete_agent` 默认实现（只读 provider 不受影响）；
- **6b 仍未做**：receptionist 温池、"正在唤醒 X…"系统消息（冷启动体验）留待后续。

### 阶段 7a — 循环解释器 ✅ 已完成（2026-07-31）

把 LoopSpec 从纯旋钮扩展为"stage 向量 + until 表达式"，LoopDriver 从硬编码 react 流程变为解释执行 spec 的 StageRunner。这是 2026-07-23 放弃的 stage 机以**受限形式**的回归：stage 是白名单枚举、Python 实现、可测试的语义单元，DSL 仍不获得发明迭代原语的自由——循环由引擎执行，DSL 只描述。

- **schema**：`:loop` 新增 `:stages`（vector of Form，每项 `(name)` 或 `(name {opts-map})`，name ∈ `{think, act}`；opts key 必须是 keyword，v1 两个 stage 均不接受 opts）与 `:until`（Form 或 EDN 源字符串，同 `:trigger/:when` 双形态）。缺省即 react 标准程序：`REACT_STAGES = [(think) (act)]`、`REACT_UNTIL = (or (iteration-limit-reached?) (error-limit-reached?))`（`agents/spec.py` 模块级常量）；`:mode :react` 保留且仍只接受 react，`:stages` 存在时优先于 mode 的隐含语义（v1 无冲突）。`to_dict`/`from_dict` roundtrip 以 EDN 字符串序列化（同 `TriggerSpec.when`）；
- **静态校验**（load 时，`SpecValidationError`）：未知 stage、非 Form 项、非 keyword opts key、v1 给 opts、空 stages 向量均拒绝；`:until` 经 `validate_form` + `LOOP_BUILTINS` + 空 scope 校验（同 `_validate_trigger_expr` 模式，表达式不取符号——循环状态经 env 传给 builtin）；
- **LOOP_BUILTINS**（`agents/eval.py`，全部 pure）：`iteration-limit-reached?` / `error-limit-reached?`（读 env 的 `state`/`limits` 段）+ 复用 `not` / `=`；env 缺键或类型不对安全默认为 False，不抛异常；
- **语义**：act 粒度 = "执行本轮全部 tool calls"，不是单个 tool call；act 是一个 Python 语义单元，跨 step/collect 两侧（dispatch 在 step 侧，收割在 collect 侧），符合 step/collect asyncio 模型。until 只在 pre-think 评估；完成（sendEmail is_complete）是 act 的事件驱动结果，不是循环边界条件，不纳入 until；**引擎硬顶 `max_iterations` 无条件生效**，无论 until 怎么写（infra 上限，"循环由引擎执行"的兜底）；until 求值失败记 `loop_until_eval_failed` warning 并回退引擎默认判断（iteration/error 上限的直接 Python 检查），保证永远停机；stages 只写 `(think)` 是合法程序——think 照跑、工具永不 dispatch，引擎在每轮末尾补推进 iteration，直至 until/硬顶终结（"他们的程序他们的选择"，不额外防护）；
- **think 失败路径变化**：record_error + advance_iteration 后直接返回，不再原地发错误邮件——下一次 step 时 until 的 `error-limit-reached?` 为真，走统一终结路径发信。外部可观测行为一致（文案相同、LLM 调用次数相同），只是晚一个调度 tick；
- **观测**：stage 执行 `loop_stage_start` debug；until 结果 `loop_until_evaluated` debug；终结 `loop_until_reached` info（带 reason = iteration_limit / error_limit）；既有 `session_max_iterations` / `session_max_errors` warning 保留。

**达成情况**：628 个测试全绿（598 既有 + 30 新增：spec 15、eval 8、loop driver 7），ruff / 架构检查 / re-export 检查全过。落地偏差：`:stages []`（空向量）在设计文本中未明确，实现按"永不推进的死循环"拒绝（SpecValidationError）；stages 缺 act 时引擎在每轮末尾补推进 iteration（否则硬顶永远不触发，与"直到 until/硬顶终结"的语义矛盾），默认 think+act 程序行为不变（act 各路径自行推进，不双重推进）。

### 阶段 7b — 上下文构建表达式化 ✅ 已完成（2026-07-31）

把 ContextSpec 从 4 个布尔/计数旋钮改为 sections 向量，ContextBuilder 从硬编码拼装变为按向量顺序解释执行。与 7a 同一铁律：DSL 只描述，引擎执行——section 是白名单枚举（`persona` / `protocol` / `knowledge` / `mail-history`）、Python 实现、可测试的语义单元。

- **schema**：`ContextSpec` 的 `persona` / `protocol` / `knowledge` / `mail_history` 四个字段全部移除，替换为单一 `sections: tuple[Form, ...]`，缺省即标准程序 `DEFAULT_SECTIONS = [(persona) (protocol) (knowledge) (mail-history 20)]`（`agents/spec.py` 模块级常量，与 7a 前默认行为逐字节等价）。`to_dict`/`from_dict` roundtrip 以 EDN 字符串序列化（同 `LoopSpec.stages`）；
- **静态校验**（load 时，`SpecValidationError`）：未知 section、重复 section、非 Form 项、非 symbol 头均拒绝；`(persona)`/`(protocol)`/`(knowledge)` 不接受参数；`(mail-history N)` 恰好一个非负 int 参数；空向量合法（无 knowledge、无历史，只剩当前邮件——"他们的程序他们的选择"）；
- **旧键兼容展开**：`:context` 节里的旧键 `persona`/`protocol`/`knowledge`（bool）与 `mail-history`（int）仍接受，语义 = 对 base.sections 向量做编辑——false 移除该 section、true 无操作、`:mail-history N` 原位替换计数（向量中无该 section 则追加，0 = 移除）；`:sections` 存在时优先，旧键全部忽略并 warning（`agent_spec_legacy_keys_ignored`）。prototype 库的 behavior.tlon（fast-responder 用 `:knowledge false :mail-history 5`）零改动，正好作为旧键展开的狗粮验证；
- **语义**：`build_context` 按向量顺序解释对话类 section——`(knowledge)` 追加 knowledge 消息，`(mail-history N)` 取邮箱历史追加（N≤0 跳过）；`persona`/`protocol` 在对话构建中跳过，改由 `get_system_prompt` 经 `has_section` 派生 `include_persona`/`include_protocol` 喂 SystemPromptBuilder。**当前邮件永远由引擎追加在最后**，不在 DSL 内（引擎职责，同 7a 的 max_iterations 硬顶）；
- **影响面**：读旧字段的点全部清扫（context_builder 三处 + 两个测试文件）；REST/wizard 无直接消费旧字段的代码（spec 对外序列化只经 `to_dict`）。

**达成情况**：658 个测试全绿（628 既有适配 + 30 新增：sections 解析/校验/roundtrip 17、旧键展开 8、builder 解释执行 5），ruff / 架构检查 / re-export 检查全过。无落地偏差。

### 阶段 7c — 新 stage 类型（plan/critique/revise + stage 条件）✅ 已完成（2026-07-31）

把 stage 家族从 `{think, act}` 扩展为 `{think, act, plan, critique, revise}`，并给所有 stage 加上 per-stage `:if` 条件——决策 2 v2 sketch 的主体形态落地。stage 仍是白名单枚举、Python 实现、可测试的语义单元；新增能力全部走"新增 stage 枚举 + 新增纯谓词"，不开放自由拓扑。

- **循环 scratchpad**：`AgentSession.loop_scratch: dict[str, Any]`——per-session 的 stage 间暂存（plan 文本、critique 裁决），引擎所有，DSL 只能经谓词读取；
- **STAGE_COND_BUILTINS**（`agents/eval.py`，全部 pure，env 缺键安全返回 False）：`critique-failed?` / `critique-passed?`（读 scratch 的 critique 裁决）、`plan-exists?`（scratch 的 plan 是非空字符串）、`first-iteration?`（`state.iteration == 0`），复用 `not` / `=`；
- **schema**：所有 stage 接受可选 `:if`（表达式，load 时经 `validate_form` + `STAGE_COND_BUILTINS` + 空 scope 静态校验，同 until 模式）；`plan` 额外接受 `:max-steps`（正 int，缺省 5）；未知 opts key 拒绝（7a 的"think/act 不接受 opts"限制随之解除为"只接受 `:if`"）；
- **stage 语义**：

  | stage | 语义 | 产出 |
  |-------|------|------|
  | `plan` | 一次规划 LLM 调用（context + 中文规划指令，`agent.plan` span） | 文本写 `scratch["plan"]`，并以 user 消息 `[执行计划]` 追加进 context |
  | `critique` | 一次评审 LLM 调用（`agent.critique` span）；首行 PASS → passed，FAIL → failed 且余下文本为 feedback，无法解析 → passed（宽容默认，不阻塞主流程） | `scratch["critique"] = {"passed": bool, "feedback": str}` |
  | `revise` | 无 LLM 调用；把 critique feedback 以 user 消息 `[评审反馈]` 追加进 context，下一轮 think 自然修正；无 feedback 时无操作 | 仅 context 追加 |

  三者都不消费也不产出本轮 LLMResponse（response 链透传）；
- **失败软跳过**：plan/critique 的 LLM 失败记 warning（`loop_stage_failed`）并跳过——不写 scratch、不 `record_error`（辅助 stage 降级，不炸 session）；`:if` 求值 `EvalError` 同样 warning + 跳过（`loop_stage_condition_failed`，安全默认不执行）；条件为 False 跳过记 `loop_stage_skipped` debug（reason=condition）；
- **策略写在程序里**：plan/critique 是否每轮都跑由程序的 `:if` 控制（如 `(plan {:if (first-iteration?)})` 只规划一次），引擎不内置任何"只跑一次"式的隐式策略；
- **狗粮**：deep-thinker prototype 改为显式程序 `[(think) (act) (critique) (revise {:if (critique-failed?)})]`（max-iterations 25 等旋钮保留）；fast-responder / archivist 不动，默认程序即 react，正好验证兼容性。

**达成情况**：691 个测试全绿（658 既有适配 + 33 新增：spec 10、eval 9、loop driver 13、prototype 1），ruff / 架构检查 / re-export 检查全过。落地偏差：无删除既有测试；两处既有 spec 测试按新语义更新断言（`test_unknown_stage_rejected` 的未知 stage 从 `(plan)` 改为 `(dance)`——plan 已转正；`test_stage_opts_rejected_in_v1` 更名 `test_stage_unknown_opts_key_rejected`，断言从"no opts"改为"unknown opts key"）。

阶段 1–2 是纯架构收益，即使"LLM 写 DSL"最终不好用也不亏；阶段 3–4 是愿景本体；阶段 5–6 让系统从"固定班底"走向"自组织的剧团"。

### 阶段 P5a — ScriptMember 骨架 ✅ 已完成（2026-08-05）

Member 的 script 类型从"手写的独一份"（亚当）泛化为**通用运行时 + 策略数据**：

- **ScriptSpec**（`agents/script_spec.py`，纯数据 + EDN 前端，total 校验）：`{:version 1 :rules [...]}`——每条规则 `{:name :on :when? :actions}`，`:on` 是事件类型 keyword 或 `:boot`（initialize 后执行一次）；
- **ScriptMember**（`agents/script_member.py`）：按规则订阅事件 → `:when` 纯谓词求值（env = `event`/`self`）→ 动作求值收集 ActionRequest → 引擎执行（发布 AGENT_START_REQUEST/AGENT_STOP_REQUEST），trace carrier 从触发事件透传；求值异常隔离到单条规则；
- **与 on-complete 同构**：动作延续 4b 的"求值纯、动作变数据、引擎执行"；查 registry 的宿主谓词（`sleeping?` 等）以闭包注入（参照 `make_actions_builtins` 工厂模式）；迭代由动作内置函数封装（`(start-all ...)` 内部遍历注册表——DSL 永不执行迭代的铁律不变）；
- **亚当翻译**：`Adam` 类删除，`make_adam()` 工厂返回挂内置 `ADAM_SPEC_EDN` 的 ScriptMember——boot/wake/drain 三条策略线从 190 行 Python 变成三条数据规则，行为不变（既有亚当测试全绿）。

**达成情况**：793 个测试全绿（723 既有 + 70 新增：script_spec、script builtins、script_member、亚当等价性），ruff / 架构检查 / re-export 检查全过。**后续（P5b，已完成）**：spawn 路径延伸 runtime 感知（react/script）、relay prototype（纯转发者，`:llm false` 的落地形态即 script member）——见下节。

### 阶段 P5b — relay prototype + spawn 路径 runtime 感知 ✅ 已完成（2026-08-05）

script 类型成员从"只有内置亚当"走向"可经 prototype/spawn 创建"，relay（纯转发者，不调 LLM）成为第一个 script prototype：

- **`AgentConfig.runtime`**（`base/config.py`）：`Literal["react", "script"]`，缺省 `react`——存量 agent.yaml 零迁移；
- **prototype 声明 runtime**：`prototype.edn` 新增可选 `:runtime` keyword（`:react` 缺省 / `:script`），`PrototypeInfo.runtime` 承载；script prototype 目录放 `script.tlon`（ScriptSpec）而非 `behavior.tlon`，人格三件套可缺（script 无 LLM、无 system prompt）；目录合格判定扩展为两种形态任一（behavior.tlon 或 script.tlon）；`instantiate()` 按 runtime 分流——script 路径拷贝 script.tlon + 写 `runtime="script"` 的 agent.yaml，react 路径不变；
- **relay prototype**（包内 `tlon/prototypes/relay/`，`:trusted true :runtime :script`）：一条规则——`:on :mail-delivered` → `(forward {:to ["gu-yanxi@tlon.internal"]})`。目标地址是字面量（v1 不做参数化模板，用户拷贝实例后改地址即可）；
- **forward 动作**（`agents/eval.py` 新增动作内置函数，与 start/stop 同类）：求值只产出 `ActionRequest(kind="forward")`，ScriptMember 原生执行——取触发事件 payload 里的 mail，创建新 Mail（from=自身地址、to/cc/subject 来自动作参数、content 原文、`forward_of=原邮件 id`），发布 MAIL_NEW，trace carrier 透传同 start/stop；
- **ScriptMember 邮件语义**（与 Agent 侧对齐）：自发邮件（`mail.from_address == 自身 address`）忽略；registry 装配的实例注入 mailbox（亚当内置实例为 None），initialize 时补扫 `mailbox.unread()`（沉睡期间到达的邮件，agent 侧对应物是 boot unread scan），处理完一封（实时或补扫）后 `mark_read`，失败仅记日志；
- **idle 语义**：`active_session_count` 恒 0；`idle_seconds` 从最近一次规则动作执行起算（无动作时从 initialize 起算）——registry 的 idle drain 对 script contractor 自然生效；
- **registry runtime 感知**：`_start_agent` 按 `config.runtime` 分流——script 路径 `ScriptSpec.load(agent_id)`（total 语义：缺失/无效拒绝启动，走既有 `agent_start_failed` 路径）→ 构造 ScriptMember（注入宿主谓词闭包 + mailbox）→ initialize → 入运行表；adopt / spawn / wake / drain / stop / 状态查询对 script 实例一视同仁（运行表、预算、角色、完工即弃全部复用）。

**落地约束**：`ScriptSpec.load` 以无宿主注册表校验——script 实例的 spec 可用纯访问器与动作形式，不可用宿主谓词（`sleeping?` 等是亚当的词汇表）。

**范围外（本阶段未做）**：writePrototype 写 script spec、mirror 的 script prototype 审批界面、script spec 参数化模板——均已随后续  落地（详见路线图「阶段 7d」下方登记项清零说明）。

**达成情况**：844 个测试全绿（793 既有 + 51 新增：test_agent_config 3、test_prototypes 7、test_eval forward 12、test_script_member 15、test_script_spec load 4、test_script_runtime 10），ruff / 架构检查 / re-export 检查全过。既有 prototype 测试按 relay 入库适配（react/script 两类内置目录分列断言），无删除既有测试。

### 方案 C · C1 — StageOutcome 基础设施 + think/plan/critique/revise 纯化 ✅ 已完成（2026-08-06）

P4（2026-08，`agents/host.py` 的窄 Protocol + 服务分解）只是**过渡形态**：它把子系统对 Agent 的隐式回指收窄为显式契约，但 LoopDriver 等子系统仍直接调用宿主的副作用委托（`_send_mail` / `_save_interaction_to_memory` / 工具 dispatch）。方案 C 把 stage 演进为**纯转换** `Session × Services → StageOutcome`：系统状态变更全部是 stage **返回的数据**，由引擎统一执行。这与 on-complete 的 ActionRequest 设计同构（阶段 4b：表达式纯求值收集 ActionRequest，引擎再逐条执行），是"DSL 只描述，引擎执行"铁律向子系统层的下沉——stage 不再伸手拿宿主能力，而是声明意图、引擎代办。最终目标：断开 Agent ⇄ LoopDriver 运行时环。

**边界原则**（stage 的外部接触三分）：

- **LLM 调用 = 只读外部查询** → 走注入服务，不算副作用，不进 Outcome；
- **系统状态变更 = 数据** → context 追加、scratch 写入、iteration/error 计数、（C2 的）发信/写记忆/工具 dispatch/完成信号，全部经 StageOutcome 返回，引擎执行；
- **日志/trace/事件 = 可观测性** → 走注入服务（不算系统状态变更）。

**C1 落地**：

- **`StageOutcome`**（`agents/stages/outcome.py`，可变 dataclass——容器字段由 stage 增量构建，冻壳不冻容器买不到真不可变）：`response`（response 链透传，非 None 时更新链上值）、`context_appends`、`scratch_updates`、`advance_iteration`、`record_error`（四轻 stage 本步只碰这五个），以及 C2 字段 `mails`（`MailEffect`）/`tool_dispatches`/`save_interaction`/`run_on_complete`/`complete`（本步一并定义以免 churn，默认全空）；
- **`StageServices`**（`agents/stages/services.py`，引擎侧构造注入）：`think(context, *, span)`——吸收原 `Agent.think` 全部语义（system prompt、tool 定义、AGENT_THINKING_START/AGENT_LLM_REQUEST/AGENT_LLM_RESPONSE/AGENT_THINKING_END 四事件 payload 逐字节保持、span 包裹、耗时统计），`traced_span(name)`、`logger`、`identity`、`spec`；全部从 Agent 的既有注入依赖装配（llm_client、context_builder、publish 闭包、tool 定义提供者），**不需要 Agent 本体**；
- **四 stage 纯化**：签名改为 `(svc, session, response, opts) -> StageOutcome`——think 成功回 `Outcome(response=...)`（`session.set_last_llm_response` 是 session 自身状态，stage 内合法）、失败回 `Outcome(record_error, advance_iteration)`；plan/critique 的 LLM 失败仍软跳过（warning + 空 Outcome），指令文案逐字保持；revise 无 LLM 调用；
- **解释器双路径**（`loop_driver.py`）：注册表分流——Outcome stage 调用后由驱动器 `_apply_outcome` 统一应用（context extend、scratch update、计数器，顺序与现状等价；think 失败以 `outcome.response is None` 判定早退，语义与旧 None 链完全一致）；`act` 保持 host 旧路径（dispatch/collect/发信/完成语义留 C2）；
- **host 瘦身**：`LoopDriverHost` 移除 `think` 与 `_traced_span`（仅四个已纯化 stage 使用）；`Agent.think`/`Agent._traced_span` 删除（全仓 grep 确认无其余调用方），LLM 语义由 `StageServices.think` 继承；`Agent._get_system_prompt` 委托保留（测试直接消费）。

**达成情况**：852 个测试全绿（844 既有适配 + 8 新增：test_stage_outcome.py 7——think 直调 2、_apply_outcome 2、step_session 集成 3，TestPlanStage 补 response 透传 1），ruff / 架构检查 / re-export 检查全过。TestPlanStage/TestCritiqueStage/TestReviseStage 改造为直调 stage 断言 Outcome 数据（方案 C 的承诺：stage 可脱离宿主单测），行为断言（span 名、指令文案、软跳过、response 透传）不放松；无删除既有测试。

### 方案 C · C2 — act 纯化 + 断开 Agent ⇄ LoopDriver 运行时环 ✅ 已完成（2026-08-06）

C2 把最后一个不纯的 stage（act）迁入 Outcome 模型，方案 C 收口。至此"DSL 只描述，引擎执行"在三个层级自相似：

| 层级 | 描述（数据） | 执行（引擎） |
|------|--------------|--------------|
| 行为 | behavior.tlon（AgentSpec，loop 程序 = stage 向量 + until 表达式） | LoopDriver 解释执行（硬顶 max_iterations） |
| 动作 | `ActionRequest`（on-complete 表达式纯求值收集 spawn/emit） | OnCompleteRunner 逐条执行（4b） |
| 步骤 | `StageOutcome`（stage 纯转换声明状态变更） | LoopDriver `_apply_outcome` + EffectExecutor |

**C2 落地**：

- **act 纯化**（`agents/stages/act.py`）：`act_stage_step(svc, session, response, opts) -> StageOutcome`——无 tool calls 回 `Outcome(context_appends=[assistant 消息+提醒 或 纯提醒], advance_iteration)`（提醒文案逐字节保持，`_add_no_tool_reminder` 变为纯数据产出 `_no_tool_reminder_appends`）；sendEmail(is_complete)+其他工具冲突回冲突提示；否则只回 `Outcome(tool_dispatches=[ToolCall...])`——**act 只声明 dispatch 什么，并发是引擎的**：逐工具 TOOL_EXECUTE_START + `asyncio.create_task` 后台执行 + `session.dispatch_tools` + `mark_waiting` 全在 `_apply_outcome`。`act_stage_collect(svc, session) -> StageOutcome`——收割（`session.collect_completed` 直调）、TOOL_EXECUTE_END/TOOL_ERROR 事件经 services 发布（可观测性）、sendEmail 回复语义（显式 `to` 优先、否则回触发邮件发件人、回复头继承——逐字保持）进 `mails`，assistant+tool 消息进 `context_appends`，is_complete → `save_interaction` + `run_on_complete` + `complete`；session 状态方法（add_tool_result/advance_iteration/mark_active）是 stage 合法输入状态直调，mark_completed 由引擎按 `complete` 标志执行。引擎应用顺序（collect 路径）：context_appends → mails → save_interaction → run_on_complete → complete——与现有时序等价（save → on_complete → mark_completed 在 session span 内，spawn 配额归因不变）；
- **`EffectExecutor`**（`agents/executor.py`，引擎侧执行 Outcome 外部效果）：`send_mail(MailEffect)`（吸收原 `Agent._send_mail` 全部语义：Mail.create + "mail.send" span + MAIL_NEW 事件 + trace 注入 + 异常记 `mail_emit_failed`）、`save_interaction`（吸收 `_save_interaction_to_memory`：spec 闸门 + memory.add_interaction + reflection.note_interaction + `memory_save_failed`）、`mark_mail_read`（mailbox 注入，永不抛）、`publish`（引擎侧 TOOL_EXECUTE_START 发布闭包）——全部从 Agent 注入依赖装配，**不持 Agent 引用**；
- **断环**：LoopDriver 最终签名 `(identity, spec, services: StageServices, executor: EffectExecutor, tool_executor, per_tool_timeout, on_complete: OnCompleteRunner, derive_state: Callable)`——不再收 host/Agent；context 首建走 `StageServices.build_context`（复用 C1 注入的 context_builder），`_session_span` 逻辑（bound_contextvars + continue_trace + "agent.session" span 含 spec_hash）移入 `StageServices.session_span`，`_terminate_loop`/超限邮件走 `executor.send_mail`，状态派生以闭包注入（Agent 的状态派生是 Agent 的职责，闭包是能力不是环）。**LoopDriver 宿主协议从 `agents/host.py` 删除，代码面 grep 清零**；Agent 侧 `_save_interaction_to_memory`/`_run_on_complete_actions`/`_tool_call_to_dict`/`_tool_result_to_string` 删除（后者两个纯序列化函数迁入 act.py），`_send_mail`/`_mark_mail_read`/`_session_span` 保留为 OnCompleteRunner/Scheduler 宿主面的薄委托（实现已下沉，无重复逻辑），`_build_context` 保留（测试直接消费）；
- **边界原则**（同 C1，act 亦遵守）：LLM=查询走服务；系统状态变更=Outcome 数据；可观测性（日志/事件/span）=服务；session 方法是 stage 合法输入状态。

**达成情况**：864 个测试全绿（852 既有适配 + 12 新增：act step 直调 5、act collect 直调 5、collect 应用顺序 1、纯 fake 断环集成 1——不经 Agent 构造 LoopDriver 跑通 think→act→complete 全程），ruff / 架构检查 / re-export 检查全过；无删除既有测试（3 个截获 `agent._logger` 的循环日志测试改截 `StageServices.logger`，13 处 `_save_interaction_to_memory` patch 随委托删除而摘除——memory 本为 mock，隔离语义不变）。

**断环收尾（2026-08-07）**：三条残留路径全部修复——`make_event_publisher` 从 event_bus 直构（publish 闭包不再持有 Agent）；OnCompleteRunner 改吃能力（identity/spec/logger/mail_sender/registry），`OnCompleteHost` 删除；状态派生反转为读时计算（`Agent.state` property 由 scheduler 存活会话集合算出 ACTIVE/THINKING，引擎零回调）。SessionScheduler 同步改为能力注入（config/memory/session_span/mark_mail_read/check_reflection/loop_driver），`SchedulerHost` 与 `agents/host.py` 随之删除——**Agent 对象图彻底无环，无任何子系统持有其引用**。死委托 `Agent._send_mail` 删除。

### 阶段 7d — 居民 behavior 个性化（狗粮）✅ 已完成（2026-08-08）

total spec 迁移时 6 个居民拿到的是同一份 canonical behavior.tlon（标准 ReAct 程序），DSL 的 trigger 表达式 / stages / until / sections 能力在真实 agent 身上一处未用。7d 给每个居民写个性化 behavior 文件——**DSL 狗粮**：在给外部 LLM 用 `writePrototype` 之前，先让真实 agent 用上这些能力，检验 ergonomic 与覆盖度（谓词够不够、写法顺不顺，只有真写才知道）。

**落地**：

- `standard_behavior_edn(agent_id=None)`：命中 6 个已知居民 id 返回个性化 EDN，否则 fallback canonical（`None` 路径字节不变，存量行为零变化）；`AgentStore.write_behavior_file` 传入 `agent_id`，新创建居民自动拿到个性化行为，已存在居民文件不受影响；
- `_resident_behavior_edn()` / `_mk_spec()` / `_ResidentSpec`（TypedDict）——紧凑 knobs→EDN 构建器，6 份行为以 `dumps()` 构建（与 canonical 同源）。

**6 位居民行为设计**：

| 居民 | 角色 | 核心差异 |
|------|------|---------|
| 林知遥 | 开发工程师 | think→plan→act→critique→revise, iter=20, history=30 |
| 江予白 | 运维工程师 | think→act, iter=5, 无 persona（系统操作不需要性格） |
| 沈清晏 | 项目经理 | think→plan→act, iter=8, plan 仅首轮 |
| 顾言蹊 | 客服专员 | think→act, iter=3, 表达式触发 |
| 苏晚晴 | 产品经理 | think→act→critique, iter=12, reflect 5min |
| 谢云舒 | 数据分析师 | think→plan→act→critique, iter=15, knowledge 前置（数据词典优先） |

**DSL 能力覆盖（狗粮达成）**：

| DSL 能力 | 使用居民 | 备注 |
|----------|---------|------|
| `:trigger :when` 表达式 | 顾言蹊 | `(or (addressed-to-me? msg) (content-matches? msg "紧急"))`——`content-matches?` + `or` 首次狗粮 |
| `:sections` 排序变化 | 江予白、谢云舒 | 无 persona / knowledge 前置 |
| `(plan)` stage | 林知遥、沈清晏、谢云舒 | 首次狗粮 |
| `(plan {:if (first-iteration?)})` | 沈清晏 | `first-iteration?` 首次狗粮 |
| `(critique)` stage | 林知遥、苏晚晴、谢云舒 | 扩展 deep-thinker |
| `(revise {:if (critique-failed?)})` | 林知遥 | `critique-failed?` 首次狗粮 |
| `max_iterations` 变化 | 全部 | 3/5/8/12/15/20 |
| `mail-history N` 变化 | 全部 | 10/15/20/25/30 |
| `reflect.interval` 变化 | 苏晚晴、谢云舒 | 5min / 30min |

**狗粮反馈**：

- **表达式触发完全取代 data 规则（语义陷阱）**：`:when` 非空时 `on_mention` 不参与（阶段 3a 已记"when 优先"，7d 验证了行为后果）——顾言蹊首版 `(content-matches? msg "紧急")` 会让客服对**所有普通点名邮件静默**，必须 `(or (addressed-to-me? msg) (content-matches? msg "紧急"))` 才能既保持点名响应又过滤内容。写作 DSL 触发时需记住：表达式模式是"完全重写"，不是"附加过滤"；
- **无新增 builtin 需求（正向）**：计划的 8 类能力全部成功表达，谓词 / `:if` / sections 排序写法顺，没有发现缺失的谓词或别扭的语法——DSL 表达能力够用，`first-iteration?`/`critique-failed?`/`content-matches?` 在真实行为中自然涌现。

**达成情况**：916 个测试全绿（+12 新增：TestResidentBehaviors 10 + agent_store 集成 2），mypy 136 文件 / ruff / 架构检查全过。

## 后续登记项

已完成阶段遗留的、已明确方向但未排期的延伸工作。登记即承诺方向，不承诺时间。

> **2026-08-08：登记项全部清零。** 五条在  登记的条目已随后续 PR 全部完成，详见路线图对应阶段：
>
> | 登记项 | 完成 |
> |--------|------|
> | 阶段 7d 居民 behavior 个性化（狗粮） | （见路线图「阶段 7d」） |
> | writePrototype 支持 script spec（P5b 延伸） |  |
> | mirror 的 script prototype 审批界面（P5b 延伸） |  |
> | script spec 参数化模板（P5b 延伸） |  |
> | mypy 豁免基线消化 |  |

## 明确不做（v1）

- 递归、`while`、`loop`/`recur`、宏
- YAML/JSON 等其他前端
- 规约内的可变全局状态
- LLM 生成规约自动激活（必须人工确认）
- **完整 stage 机循环（原阶段 3b）**：已以**受限 stage 向量**形式回归（阶段 7a，2026-07-31；stage 家族 7c 扩为 `think`/`act`/`plan`/`critique`/`revise` 并支持 `:if` 条件）——stage 是白名单枚举、Python 实现、可测试的语义单元，`:until` 是纯谓词表达式。"LLM 发明全新循环拓扑"的完整 stage 机仍不做：若未来出现 react 无法表达的真实需求，以**新增 stage 枚举**（Python 实现）回应，而非开放自由拓扑——7c 的 plan/critique/revise 正是这条路径的第一次兑现
