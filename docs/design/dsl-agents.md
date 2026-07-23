# DSL 书写 Agent

> 状态：设计定稿（2026-07-22 讨论收敛）· 阶段 1 已完成（2026-07-22）· 阶段 2 已完成（2026-07-22）· 阶段 5 已完成（2026-07-22）· 阶段 6a 已完成（2026-07-22）· 阶段 6b 已完成（2026-07-23）
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

每个 stage 是 Python 实现、可测试的语义单元。

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

## 语言设计（分层落地）

**L0 — EDN 数据字面量（已落地，`agents/edn.py`）**：map / vector / keyword / string / number / bool。v1 规约文件是纯数据，只解析不求值；trigger/loop/context 全部为声明式数据。

v1 落地的真实 schema（`~/.tlon/{agent_id}/config/behavior.tlon`，实现于 `agents/spec.py`）：

```clojure
{:version 1                        ; 必填，只接受 1
 :trigger {:on-mention true        ; 被 @ 必响应
           :proactive false}       ; DefaultSpec 取自 AgentConfig.proactive
 :context {:persona true           ; IDENTITY/SOUL/RULES + 团队 SOUL 段
           :protocol true          ; 框架交互协议段
           :knowledge true         ; 记忆知识块注入
           :room-history 20}       ; 房间历史条数，0=关闭
 :loop {:mode :react               ; v1 仅 :react，其他值报错
        :max-iterations 10         ; DefaultSpec 取自 AgentConfig.max_iterations
        :tools :background         ; v1 仅 :background
        :max-consecutive-errors 3} ; DefaultSpec 取自 AgentConfig.max_consecutive_errors
 :on-complete {:save-interaction true              ; 会话完成后写入记忆
               :reflect {:enabled true             ; 反思开关（初始值，运行时仍可切换）
                         :interval-minutes 15}}}   ; DefaultSpec 取自 memory config
```

规则：缺省字段继承 DefaultSpec（由 agent 的 config 派生），所以部分文件是纯覆盖；未知 key 忽略并 warning（向前兼容）；类型错误报带字段路径的清晰错误。keyword / symbol / string 是三种不同类型（`:mode :react` ≠ `:mode "react"`）。

**L1 — 受限求值器（后落地）**：

- 特殊形式白名单：`if` / `when` / `let` / `and` / `or` / `match`，仅此而已；无 `loop`/`recur`、无 `def`、初期无宏；
- 领域内置谓词与动作：`mentioned?` / `sender-type` / `tool-allowed?` / `call-tool` / `spawn` / `emit` / `score-memories` 等；
- **fuel 限制**：求值带步数上限与深度上限；
- 数据化 schema 校验（malli 风格，校验器本身也是数据）。

示意（非终稿语法）：

```clojure
;; reviewer.tlon —— 一份 agent 行为规约
{:trigger (when (and (mentioned? msg)
                     (= (sender-type msg) "human"))
            :fire)
 :context {:system-prompt (file "persona/reviewer.md")
           :memory {:working 10 :episodic {:top-k 5}}}
 :loop {:mode react :max-iterations 10 :tools :background}
 :on-complete [(reflect {:every-n-sessions 5})
               (when (task-done? result)
                 (spawn "archiver.tlon" {:msg (summarize result)}))]}
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
├── deep-thinker/          # 深度思考者（:max-iterations 大、knowledge 全开）
│   ├── behavior.tlon
│   ├── IDENTITY.md / SOUL.md / RULES.md
├── fast-responder/        # 快速反应者（迭代少、context 精简）
├── relay/                 # 消息转发者（极简 context，只路由）
└── archivist/             # 档案管理者（reflect 频繁、记忆工具全开）
```

设计决策：

- **继承机制现成**：阶段 2 的 `AgentSpec.load(base=...)` 部分覆盖语义即 prototype 继承——prototype 是 base，实例 `behavior.tlon` 只写差异字段；
- **拷贝而非引用**：实例化时把 prototype 内容复制为实例自有文件，行为冻结在创建时刻——Tlon 升级改了 prototype，不影响存量 agent；
- **存放**：包内预制，首次运行拷贝到 `~/.tlon/prototypes/`（与 `default_team.py` 种子机制同构），用户可改可加；
- **发现**：人类走 `tlon mirror` 创建菜单；agent 走 `list_prototypes` 工具；
- **已知缺口**：纯转发者不需要 LLM，v1 schema 的 `:loop {:mode :react}` 之外可能需要 `:llm false` 逃生门——推迟到实现 relay prototype 时再加。

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
- 工具权限不可升格：只允许从 prototype 库 spawn（Tlon 预制 prototype 为可信源；LLM 现写的规约仍走人工批准闸，是阶段 4 的事），overrides 仅 behavior 字段，tools 不在 spec schema 内故天然无法升格。

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

### 阶段 3 — 受限求值器

白名单特殊形式 + 领域 builtin + fuel 限制 + 数据化 schema 校验；trigger 条件升级为表达式；循环模式开放 stage 机编排。

完成标准：同一行为可用纯数据与含表达式的规约各写一遍，解释执行结果一致。

### 阶段 4 — LLM 生成与 spawn 互触发

agent 获得"编写/改写规约文件"的工具（校验 → dry-run → 人工确认 → 激活）；`spawn` 实现规约互触发 + 全局深度/速率限制；trace 记录规约版本哈希。

完成标准：用户可通过对话让 agent 为自己造一个新 agent 并投入运行。

### 阶段 5 — Behavior Prototype 库 ✅ 已完成（2026-07-22）

包内预制 prototype（spec + 人格模板 + 工具白名单），首启拷贝至 `~/.tlon/prototypes/`；实例化走"拷贝 + 部分覆盖"；`tlon mirror` 菜单与 `listPrototypes` 工具两条发现路径。依赖阶段 2 的 base 覆盖语义（已完成）。

完成标准：从 `deep-thinker` prototype 创建新 agent，行为继承 + 覆盖生效，测试全绿。**达成情况**：331 个测试全绿（311 既有 + 20 新增 prototype 测试），ruff / 架构检查 / re-export 检查全过。落地情况：

- 包内 `tlon/prototypes/` 首批 3 个 prototype（deep-thinker / fast-responder / archivist），每个含 `behavior.tlon`（只写与 DefaultSpec 的差异字段）+ 人格三件套 + `prototype.edn` 元数据（自家 edn.py 解析）；hatchling 默认将包数据打入 wheel，已构建验证；
- `agents/prototypes.py`：`seed_prototypes()`（幂等，不覆盖用户修改）、`list_prototypes()`（读 `~/.tlon/prototypes/`，用户增改可见）、`instantiate()`（人格文件拷贝 + behavior 在 EDN 数据层深合并 overrides 后落盘 + 走标准 `_yaml_save` 写 agent.yaml；`AgentConfig.prototype` 记录来源）；
- ServerApp 初始化早段种子；wizard `agent_menu` 加"从 Prototype 创建"入口；`listPrototypes` 工具注册进每个 agent（工具总数 17 → 18）；
- 实例化不做 registry 热加载，重启 dream 后生效（与 wizard 现有创建行为一致）；
- **relay 推迟**：纯转发者不需要 LLM，需要 v1 schema 之外的 `:llm false` 逃生门（见上文"已知缺口"），本次只交付 3 个 prototype，relay 随逃生门一并落地。

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

阶段 1–2 是纯架构收益，即使"LLM 写 DSL"最终不好用也不亏；阶段 3–4 是愿景本体；阶段 5–6 让系统从"固定班底"走向"自组织的剧团"。

## 明确不做（v1）

- 递归、`while`、`loop`/`recur`、宏
- YAML/JSON 等其他前端
- 规约内的可变全局状态
- LLM 生成规约自动激活（必须人工确认）
