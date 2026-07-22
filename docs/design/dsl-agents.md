# DSL 书写 Agent

> 状态：设计定稿（2026-07-22 讨论收敛）· 尚未开始实现
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

**L0 — EDN 数据字面量（先落地）**：map / vector / keyword / string / number / bool。v1 规约文件是纯数据，只解析不求值；trigger/loop/context 全部为声明式数据。

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

## 路线图

### 阶段 1 — 拆分 agent.py（P1 ）

拆为：`Agent` 薄壳（编排）+ `SessionScheduler`（主循环）+ `LoopDriver`（react 步进/收割）+ `TriggerEvaluator` + `ContextBuilder`（并入 prompt_builder）+ `ReflectionEngine`。顺手补：session 最大轮数/空闲超时、消息队列背压、`error_count` 消费。

完成标准：行为不变，测试全绿 + 真实冒烟通过。

### 阶段 2 — AgentSpec + EDN 数据文件

定义 `AgentSpec` dataclass（纯数据字段）；写 `DefaultSpec` 精确复刻当前行为；Agent 改为"读 spec 驱动组件"的解释器；规约文件为纯 EDN 数据字面量（只解析不求值）。

完成标准：现有 agent 配置等价迁移，行为不变，测试全绿。

### 阶段 3 — 受限求值器

白名单特殊形式 + 领域 builtin + fuel 限制 + 数据化 schema 校验；trigger 条件升级为表达式；循环模式开放 stage 机编排。

完成标准：同一行为可用纯数据与含表达式的规约各写一遍，解释执行结果一致。

### 阶段 4 — LLM 生成与 spawn 互触发

agent 获得"编写/改写规约文件"的工具（校验 → dry-run → 人工确认 → 激活）；`spawn` 实现规约互触发 + 全局深度/速率限制；trace 记录规约版本哈希。

完成标准：用户可通过对话让 agent 为自己造一个新 agent 并投入运行。

阶段 1–2 是纯架构收益，即使"LLM 写 DSL"最终不好用也不亏；阶段 3–4 是愿景本体。

## 明确不做（v1）

- 递归、`while`、`loop`/`recur`、宏
- YAML/JSON 等其他前端
- 规约内的可变全局状态
- LLM 生成规约自动激活（必须人工确认）
