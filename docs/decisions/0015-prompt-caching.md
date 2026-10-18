# ADR 0015：主动利用 LLM 前缀缓存——度量先行、稳定段靠前、显式断点

> 阅读定位：本文保留当时的选择与理由；当前设计见 [runtime](../design/runtime.md)。当前适用范围：前缀缓存与上下文排序选择。完整关系见 [决策索引](README.md)。

状态：已接受（2026-09-16，方向讨论拍板）

## 背景

LLM 供应商按请求前缀缓存是否命中区别定价（命中价通常为未命中的 0.1×–0.5×），命中条件是请求前缀逐字节相同。供应商分两类机制：

- **自动前缀缓存**（OpenAI、DeepSeek、Moonshot/Kimi、GLM 等）：客户端零标记，服务端自动检测重复前缀；命中字段在 usage 里返回（名字各家不同）。
- **显式断点缓存**（Anthropic 系：anthropic / bedrock / vertex_ai）：必须在消息上打 `cache_control` 断点，否则完全不缓存。

盘点现状（2026-09）：Tlon 的消息构造在**会话内**碰巧前缀稳定（system prompt 无动态内容、锚点构建后不动、只尾部 append），但**跨会话复用基本为零**——锚点每会话重建，且每会话必变的 `thread-recap` 排在相对稳定的 `mail-history` 之前，断裂点之后的段全部按未命中计费。同时 ledger 不记任何缓存字段，命中率完全不可见；Anthropic 系模型则一个断点都没有。

litellm 1.98 已提供所需基础设施：各家缓存 usage 字段归一化到 `usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens`；Anthropic 路径透传 content block 上的 `cache_control` 并自动带 beta header。

## 决策

### 1. 度量先行（无行为变化）

`LLMClient` 从归一化后的 usage 提取 `cached_tokens` / `cache_write_tokens`，写入 ledger record 与 tracing span（`llm.usage.cached_tokens` / `llm.usage.cache_write_tokens`）。一条代码路径覆盖所有厂商。一切缓存优化的效果以 ledger 数据为准。

### 2. 上下文 section 按变动频率排序

`:context :sections` 向量按缓存友好原则排列：**几乎不变的段靠前，尾部增长的段居中，每会话必变的段与当前邮件靠后**，让前缀断裂点最大化后移。内置原型统一为：

```
(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history N) (thread-recap)
```

各段变动频率（均已核实源码）：`lessons`/`skills` 几乎不变；`knowledge`/`mail-history` 尾部增长（窗口滑动才断前缀）；`current-task` 任务流转时整体替换；`thread-recap` 每会话必变；当前邮件每次必变。位置语义有两处引擎保留：当前邮件永远最后（引擎拥有该位置）；`knowledge` 自 ADR 0017 阶段 4（2026-09-17）起固定为整个 context 的最后一条（当前邮件之后、anchor 之外）——它在 `:sections` 向量里的声明位置不再决定注入位置，只决定是否注入。knowledge 每 session JIT 重检索、内容本就在变，冻结在 anchor 里反而每 session 毁前缀；移出后 anchor 更小且跨 session 更稳定，缓存命中改善。其余段 DSL 引擎语义不变——顺序仍是 spec 声明的向量顺序，重排是原型层的策略选择。

### 3. Anthropic 系显式断点

`LLMClient.chat()` 新增 `cache_prefix_len` 参数（调用方声明稳定前缀长度，会话循环传 `session.anchor_len`）。仅当 provider ∈ {anthropic, bedrock, vertex_ai} 时注入两个断点（上限四个）：

- **system 消息**——Anthropic 缓存层级 tools → system → messages，system 断点同时覆盖工具 schema；
- **锚点最后一条消息**——护住跨会话稳定前缀。

注入在请求副本上进行，调用方消息与 journaled session context 永不带标记。自动缓存厂商一律不注入（外来字段有 400 风险）。`distill` 与压缩摘要不传 `cache_prefix_len`（非循环热点，无前缀概念）。

### 明确不做

- **压缩机制不动**：prune/摘要替换中段会打断锚点之后的前缀，但压缩是低频事件，断一次后重新累积；改为前缀保持式需重设摘要机制，收益不成比例。
- ~~**mail-history 窗口滑动不优化**：>N 封后丢弃最老邮件会断前缀；改 per-thread 历史是更大的行为变更，留待度量数据证明值得后再议。~~（已由 ADR 0025 接管：度量数据齐备后落地两段式——冻结段最早 F 封 + 滑动段最近 R 封，2026-10-07）
- **不引入 litellm 客户端响应缓存**（`litellm.cache`）：整响应缓存，与前缀缓存无关。
- **不以 `supports_prompt_caching` 注册表作门控**：litellm 注册表数据不全（moonshot 报 False 但供应商实际支持自动缓存），门控用 provider 前缀硬判定。

## 后果

- 缓存命中率首次可度量：ledger 按 `purpose`/`model` 维度可查 `cached_tokens`，后续优化的效果有据可依。
- 跨会话复用从「system prompt + tools」扩展到「system + tools + 稳定锚点段」；同线程往复时上一会话的整段历史（逐字节）成为新会话的前缀。
- Anthropic 系模型从「完全不缓存」变为「system+tools+锚点三段缓存」；其余厂商行为不变。
- 单测锁定不变式：断点只注入显式缓存厂商、调用方消息不被原地修改、同线程第二封信的 context 逐字节包含第一封信的 context 作为前缀。

## 补注

- **2026-10-11（ADR 0028）**：「当前邮件」不复存在——触发邮件本体不再注入 prompt，session 触发语是固定模板「你有 N 封未读邮件」。正文中「当前邮件每次必变」「当前邮件永远最后（引擎拥有该位置）」相应改写为：触发语永远最后（引擎拥有该位置），逐 session 只变其中的 N——模板主体逐字节稳定，前缀缓存命中其前的全部内容。
