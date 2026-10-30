# ADR 0016：LLM 配置成员级化——删除进程级 LLM_* 设置

> 后续变更（2026-10-31）：Operator TUI、Python SDK 与 Telegram 已[退役删除](legacy-client-retirement.md)；下文涉及它们的实现和验证记录属于历史。当前客户端为 WebUI，包结构与入口以[当前架构](../architecture.md)为准。

> 阅读定位：本文保留当时的选择与理由；当前设计见 [infrastructure](../design/infrastructure.md)、[webui](../design/webui.md)。当前适用范围：成员级模型配置；不自动创建标准团队的条款已由 [ADR 0036](0036-initial-society.md) 覆盖。完整关系见 [决策索引](README.md)。

状态：已接受（2026-09-16，方向讨论拍板；修订 ADR 0012）

## 背景

ADR 0012 把 LLM 配置统一到进程级 `LLM_*` 环境变量，后续迭代补了成员级 `llm.model_name` 覆盖与 per-provider key 路由。运行一段时间后暴露三个问题：

1. **进程级默认是静默兜底**：`LLM_MODEL` 默认值是已退役型号；成员没配模型时不报错、悄悄用它跑——掩盖配置缺失。
2. **进程级 temperature 制造假功能**：admin API 与 WebUI 都把 `llm.temperature` 写进 agent.yaml，但装配时明确丢弃文件值（`_build_llm_config`），改了不生效。
3. **spec 里声明模型是基因库污染**：模型选择是部署/资源策略（机制/策略分离，ADR 0003），不是认知基因；型号退役时写死模型的原型全体感染。

同时，litellm 自身已提供我们手工重写的两层能力：per-provider key 环境变量（`{PROVIDER}_API_KEY`，litellm 原生读取）与模型注册表（`get_model_info` / `model_cost`：上下文窗口、价格、缓存与工具能力）。自维护的 `LLM_API_KEY` 单 key 路由与注册表绕行是多余层。

## 决策

### 1. settings.py 删除全部 `LLM_*` 字段

`LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_TEMPERATURE` / `LLM_MAX_RETRIES` / `LLM_BASE_DELAY` 全部删除。settings 回归其本职：进程级基础设施与密钥（admin token、Telegram、OTel、日志等）。

- **API key**：完全交给 litellm 原生约定——`provider/model` 形式的模型由 litellm 读 `{PROVIDER}_API_KEY`（如 `DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY`）。`LLMClient` 不再显式传 `api_key`；发请求前做轻量 preflight：provider 前缀对应的 env 缺失即报指明变量名的错。
- **base_url escape hatch 成员级化**：`LLMConfig` 新增持久化字段 `api_base`（可选）；私有部署/代理在成员 agent.yaml 配 `model_name: openai/<model>` + `api_base: <url>`。裸模型名的进程级绕行删除。
- ~~**temperature 回归文件值**：agent.yaml 的 `llm.temperature` 生效；未声明则不向 litellm 传该参数（供应商默认）。~~ **已被后续拍板覆盖**：temperature 配置项整体删除（含配置与全部调用点），请求永不携带 temperature，供应商默认生效。
- **max_retries / base_delay / request_timeout**：不再是环境变量，用 `LLMConfig` 的代码默认值。

### 2. 模型成员级强制

`_build_llm_config` 不再有进程级回退：`agent.yaml` 未声明 `llm.model_name` 的成员在**装配期硬报错**，错误信息指明去 agent.yaml 或 WebUI 配置。成员创建的唯一漏斗 `instantiate()` 增加 `model` 参数：

- admin API `POST /admin/config/agents` 的 body 必填 `model`；
- spawn 的子成员默认继承父成员的模型（成本/能力画像随谱系，ADR 0014 的选择压语义）；
- ~~**bootstrap 不再自动播种标准团队成员**（首次启动由人类经 WebUI/admin API 创建成员并选模型）。~~ 已由 [ADR 0036](0036-initial-society.md) 覆盖：全新实例首启创建 30 位初始成员并明确写入成员级模型；已有实例不补种。`default_team.py` 保持退役，初始人物与行为原型分开管理。

### 3. spec 删除 `:llm` 声明

`behavior.tlon` 顶层 `:llm` 键从合法键集中移除，加载即报错（显式失败，不静默忽略）；`MemberSpec.llm_model` 字段与 `instantiate` 的基因型传递链删除。**已落地**：`MemberSpec.from_dict` 对顶层 `:llm` 残留直接抛 `MemberSpecValidationError`（[member_spec.py](../../src/tlon/agents/member_spec.py)），仓内原型与文档已同步清理。

### 4. 模型元数据端点 + WebUI 下拉

新增 `GET /admin/llm/models`：从 litellm `model_cost` 注册表（4000+ 条目）输出 chat 模型的 provider、上下文窗口、输入/输出/缓存读价、`supports_prompt_caching`、`supports_function_calling`。WebUI 成员编辑表单的模型输入改为可搜索下拉，展示上述元数据；SettingsView 的 LLM 区块删除（进程级 LLM 设置已不存在）。**已落地**：端点见 [admin_llm.py](../../src/tlon_server/api/rest/admin_llm.py)，WebUI 创建成员时的模型选择即走该注册表。

## 后果

- 配置真相只剩一层：成员 agent.yaml。没有静默兜底，没有写了不生效的假旋钮。
- 密钥管理回归供应商/工具链原生约定（litellm 生态文档直接适用），Tlon 不再维护 key 路由代码。
- 历史开箱流程（已由 ADR 0036 覆盖）：首次启动是空社会 + 原型库，人类在 WebUI 创建成员时选模型——以一步手工换取配置显式性。
- quickstart / README / config-reference / ADR 0012 同步修订（ADR 0012 被本文废止的部分用删除线标注）。
