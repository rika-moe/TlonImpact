# ADR 0012：引入 LiteLLM 作为 LLM 传输层 + provider 预设（方案 B）

> 阅读定位：本文保留当时的选择与理由；当前设计见 [infrastructure](../design/infrastructure.md)。当前适用范围：LiteLLM 传输层保留；进程级 LLM 配置由 ADR 0016 替代，成员字段见配置参考。完整关系见 [决策索引](README.md)。

- 状态：已接受（2026-08-30，方向讨论拍板）
- 相关：[0008-context-pressure.md](0008-context-pressure.md)（context window 驱动压缩）、[0007-session-event-sourcing.md](0007-session-event-sourcing.md)（LLMResponse journal 序列化契约）

## 背景

各家 LLM 服务商有自己的 base_url、model name、context window，用户需要逐一查询并手工配置（`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME` + agent.yaml 的 `context_window`），繁琐且易错。同时 `llm_client.py` 的 openai SDK + 可配 base_url 只能接 OpenAI 兼容端点，Claude native、Gemini、Bedrock 等非兼容 API 无法接入。

业界已有成熟方案：[LiteLLM](https://github.com/BerriAI/litellm)（~46k★）内置各 provider 的 base_url 路由表与社区维护的模型元数据注册表（context window / 价格 / 能力），按 `provider/model` 前缀自动路由，异常体系兼容 openai SDK（`litellm.ContextWindowExceededError` 等直接对应现有错误处理）。

## 决策

引入 LiteLLM，分两个阶段、各自独立 PR（行为不变先行，能力扩张随后）：

**阶段 1（传输层替换，配置面不动）：**

- `pyproject.toml` 引入 `litellm`（锁定版本），移除直接依赖 `openai`（litellm 传递依赖它）
- `llm_client.py` 内部把 `openai.AsyncOpenAI.chat.completions.create` 换成 `litellm.acompletion`；现有指数退避重试循环保留（litellm 异常继承 openai 同名类），新增 `litellm.ContextWindowExceededError` → Tlon `ContextWindowExceededError` 的映射
- 用户配置方式不变

**阶段 2（provider 预设，用户只填 token）：**

- ~~配置简化为 `LLM_MODEL=<provider>/<model>`（如 `deepseek/deepseek-chat`）+ 各家约定的 key 环境变量（`DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY` 等，litellm 按前缀自动匹配）；不再需要手配 base_url~~ **已被 ADR 0016 修订**：进程级 `LLM_*` 配置全部删除——模型是成员级配置（agent.yaml `llm.model_name`，创建成员时选择），key 只剩 litellm 原生 `{PROVIDER}_API_KEY` 约定
- `context_window` 启动时从 `litellm.get_model_info()` 自动填充，agent.yaml 中的值降级为可选覆盖（元数据缺失/滞后时的兜底）
- ~~`OPENAI_BASE_URL` 保留为 escape hatch（私有部署 / 代理 / 注册表未收录的 provider）~~ **已被 ADR 0016 修订**：escape hatch 成员级化为 `LLMConfig.api_base`（`openai/<model>` + 成员级端点），进程级 base_url 删除
- 更新 `.env.example`、Settings 视图、`admin_agents.py` 的配置写入路径、配置指南文档与 AGENTS.md 快速开始

**无论哪个阶段都保留（Tlon 编排契约，LiteLLM 无法取代）：**

- `LLMResponse` / `ToolCall` dataclass 及其 `to_dict` / `from_dict`——session journal 持久化格式（ADR 0007），必须自己控制
- `ContextWindowExceededError` → LoopDriver 强制压缩后重试一次的编排（ADR 0008）——litellm 只提供异常识别，压缩重试是引擎行为
- OTel span 属性约定与结构化日志格式

## 备选方案

**方案 A（只换传输层）。** 最小风险，但不解决"用户只填 token"的核心诉求——base_url 与 context_window 仍需手配。被方案 B 包含（B 的阶段 1 即 A），不单列。

**方案 C（B + fallback 路由 / 成本统计 / 分角色模型）。** fallback 路由（Router）与成本统计（`completion_cost`）有价值，但分角色模型会改变 agent 行为（便宜模型做 summary 的质量未实测），属于未验证的复杂度。B 稳定后可单独立项。

**models.dev 快照 + 自写薄解析层。** 数据干净、语言无关（对 JVM 迁移友好），但要自己维护快照更新机制与解析代码，且解决不了非兼容 provider 的协议差异（那部分 litellm 已经写好）。litellm 的元数据注册表覆盖同一诉求，不重复造。

**aisuite / any-llm / OpenRouter。** aisuite 覆盖 provider 少且无元数据注册表；any-llm 较新、生态未验证；OpenRouter 是托管服务（流量过第三方、有加价），Tlon 是用户自托管软件，不应把 LLM 流量强制导向中间商。

## 后果

**换得的收益：**

- 用户配置从"查文档填 base_url + model name + context window"简化为"填 token + 选 provider/model"
- 非 OpenAI 兼容 provider（Claude native、Gemini、Bedrock、Azure）可接
- context window 自动填充使 ADR 0008 的压缩阈值免于手配与试错

**接受的代价：**

- litellm 依赖体积大、版本间有 breaking change 记录——锁版本，升级单独评估
- 元数据注册表对新发布的国产模型可能滞后——agent.yaml 的 `context_window` 覆盖兜底；~~`OPENAI_BASE_URL` escape hatch 兜底~~（已被 ADR 0016 修订为成员级 `llm.api_base`）
- litellm 自带的重试/fallback/日志默认行为与 Tlon 现有重试循环叠加的风险——阶段 1 明确：重试由 Tlon 侧控制，litellm 的 `num_retries` 置 0，避免双重退避
- JVM 迁移时这层要换成 Java 生态对应物（如 LangChain4j），但 `LLMConfig` 的字段语义（provider/model + token）可平移，与 ADR 0011 同理：配置即资产

## 修订（实施定稿，2026-08-30，阶段 2 实施）

阶段 2 落地时定稿的与上文不同的细节（原文相关行保留不动，以本节为准）：

- ~~**env 变量统一定为 `LLM_*`**：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TEMPERATURE` / `LLM_MAX_RETRIES` / `LLM_BASE_DELAY`，不留 `OPENAI_*` 兼容别名（项目规范 1：不保留向后兼容）。~~ **已被 ADR 0016 修订**：六个 `LLM_*` 变量全部删除，settings 不再持有任何 LLM 配置。
- ~~**escape hatch 是 `LLM_BASE_URL`**（对应上文"`OPENAI_BASE_URL` 保留为 escape hatch"一行）：仅当 `LLM_MODEL` 为裸模型名（不含 `/`）时生效——`llm_client.py` 给裸名加 `openai/` 前缀并以 `LLM_BASE_URL` 为 api_base，用于私有部署 / 代理 / 注册表未收录的 provider。`provider/model` 形式原样传给 litellm，由其内置路由表解析 base_url。~~ **已被 ADR 0016 修订**：裸模型名路径删除（preflight 硬报错），escape hatch 成员级化为 `LLMConfig.api_base`。
- ~~**单 key 通用**：`LLM_API_KEY` 无论什么 provider 都显式以 `api_key=` 传给 `acompletion`，不要求用户按 litellm 惯例设 `DEEPSEEK_API_KEY` 等 per-provider 变量（对应上文"各家约定的 key 环境变量"一行——实施时简化为单 key）。~~ ~~**已被后续迭代修订**：多模型混居要求 per-provider key 路由——`provider/model` 形式的模型先查 `{PROVIDER}_API_KEY`（provider 段大写、`-`→`_`），未配置才回退 `LLM_API_KEY`；裸模型名路径不变。~~ **已被 ADR 0016 修订**：`LLMClient` 不再显式传 `api_key`，litellm 原生读 `{PROVIDER}_API_KEY`（preflight 缺 key 即报指明变量名的错），`LLM_API_KEY` 回退删除。
- ~~**默认模型** `moonshot/kimi-k2.5`（litellm 1.98 注册表确认 Moonshot 的 provider 名为 `moonshot`，含 `kimi-k2.5` 条目）。~~ **已被 ADR 0016 修订**：进程级默认模型是静默兜底，已删除——成员未声明模型在装配期硬报错。
- **context_window 自动填充**：`LLMConfig.context_window` 默认 `None`（= 自动），组装时解析——agent.yaml 显式值优先，其次 `litellm.get_model_info()` 的 `max_input_tokens`，查不到回退 262144；运行时消费处拿到的永远是解析后的 int。
