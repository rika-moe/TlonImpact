# Operation 统一与旧机制审计

> 历史审计档案：下文的“当前”均指 main `f5a8faf`（#194），不代表现行运行时。Capability/复合 Operation 已由 ADR 0045 删除；P5 进一步删除 v1 rules、条件作用域、Operation.control 与 stage 引擎。现行接口见 [DSL reference](../reference/dsl-reference.md)、[Operation reference](../reference/operations-reference.md) 和 [普通工作程序](programmable-workflows.md)。本页保留当时证据及决策背景。

> 后续覆盖：P5 后续清理已将四个注册表查询改为普通 Operation，并删除独立 effects 执行路径、旧写入 API 与 handler 重复投影。历史读取和未完成工作阻断保留；当前范围见 [退役代码清理](workflow-retirement-cleanup.md)。下文的宿主查询例外仅是当时状态。

状态：P8a 的独立审计，基线为 main `f5a8faf`（#194）。P1–P6 已合并；P7 暂停，原 P8 未整体完成。本文件记录源码证据与已知边界，不把符号搜索当成功能验证。本次完整质量门及真实邮件冒烟结果在实施计划和 PR 记录。

## 决策与范围

为保持模型请求前缀稳定，tools 尽量保持固定；不同成员可按其装配服务拥有不同的稳定工具集。暂停 P7 的逐请求动态 ToolView、统一 allowlist 和受控业务阶段。本次不新增授权机制，也不把隐藏工具视为权限检查。已存在的级别、成员身份、见习限制和领域约束继续生效。

P8a 只收口当前文档、核查旧实现是否仍运行，并列出未完成项。原 P8 的 Prototype 显式依赖声明与完整动态撤权验收不属于本次交付；P7 暂停不意味着这些能力已经完成。

## 审计结果

| 检查项 | 当前证据与结论 |
|---|---|
| 旧 Tool 基类及业务实现 | `src/tlon/tools/base.py` 只定义 `ToolResult`；`src/tlon/operations/adapters.py::OperationTool` 投影共享名称、描述、schema，执行委托 `OperationExecutor.call`。`src/tlon/agents/tool_executor.py::_execute_inner` 仅选择投影、跟踪和转换协议错误，不定义第二套业务实现。保留这些组件是协议分层，不能因名称含 Tool 就删除。 |
| `_HOST_QUERIES`、ProgramEffects、MemberCapabilities | 生产源码中无这些旧标识；当前领域定义在 `src/tlon/operations/`，运行装配使用 MemberServices。文件名 `src/tests/agents/test_program_effects.py` 仍表示程序效果回归，不是同名旧执行器仍存在的证据。 |
| 控制操作名单 | `operations/control.py::control_names`、`operations/dsl.py::DSLAdapter.control_names` 与 `agents/tool_executor.py::control_names` 都读取 `Operation.control`；无 CONTROL_NAMES 或 ENDING_TOOLS 常量。`test_composites.py::test_control_flag_blocks_direct_and_hidden_wrapping_without_name_list` 验证任意新控制名不能被复合包装。 |
| Capability v1 | `operations/composite.py::CompositeSpec.from_edn` 与 `agents/capabilities/spec.py::CapabilityArtifact.from_edn` 要求 version=2 和单对象 input/output schema；不保留旧 args/pure 解析器。`test_composites.py::test_artifact_immutable_single_schema_and_no_legacy_format` 与 `capabilities/test_spec.py::test_reject_invalid_artifacts` 覆盖拒绝旧格式。Behavior 的 version=1 是另一份格式，不能一并误删。 |
| Capability 审批路由 | 当前路由是 `/admin/capabilities/{name}/approve`；`server/test_rest_admin_capabilities.py::test_approval_route_is_canonical_without_staging_alias` 明确断言旧 staging 审批别名不存在。Prototype 自己的 staging 审批路由仍有效，与此无关。 |
| 三处条件词表与隐式谓词 | `agents/vocabulary.py::condition_builtins/validate_condition/evaluate_condition` 共用纯投影；RULE_SCOPE、LOOP_SCOPE、STAGE_SCOPE 是不同数据形状，不是分裂函数名单。`agents/test_conditions.py` 覆盖同一复合操作三处调用、局部 fn 隐藏效果、传递读取、缺数据与纯 control 拒绝。外部只读操作仍非纯。 |
| Builtin.read_only | `src/tlosure/builtins.py::Builtin` 当前使用 pure；条件不读取 read_only。Docker sandbox 的 read_only 参数和对应测试是文件系统安全约束，继续保留。 |
| 复合执行与冻结 | `operations/composite.py::compile_composites` 推导依赖、效果及内容身份；DSL 和 Tool 使用同一个 OperationExecutor。`agents/test_frozen_definitions.py` 验证删除当前库定义后恢复原根、无服务拒载及原子冻结；它不是旧源码 fallback，也不允许旧快照准入新工作。 |
| 参考文档生成与分层 | `scripts/gen_reference_docs.py` 从操作定义生成 Tool/Operation 表，从现存词表生成 DSL 表；`--check` 是质量门。`pyproject.toml` 的 import-linter 固化 operations 不依赖 agents、tlosure 独立等边界。搜索不能替代实际运行这两项门禁。 |

## 仍活跃的例外与后续决策

### DSL 专用宿主查询仍未全部统一成 Operation

`agents/vocabulary.py::ScriptHostQueries` 和 `make_member_builtins` 仍注入 `sleeping?`、`running?`、`lazy?`、`member-roster`，由 `agents/agent_registry.py::RegistryQueries` 查询实时注册表。它们标记 pure=false，不能进入 when/until/stage-if；它们也不经 OperationExecutor 的逐调用回执，不能作为已编译 Composite 的操作依赖。

这是活跃路径，不能写成“所有外部读取已经统一到 Operation”。`member-roster` 返回格式化的公开路由文本，`member-list` 返回操作契约的数据，两者不是可以直接删除一个的同名兼容入口。本次记录其边界；是否将这些读取转成有回执的操作、或保留有明确语义的宿主编排查询，应单独设计并覆盖 Adam 的调度策略。P8a 不更改运行行为。生成器继续提取 ScriptHostQueries，属于现存文档来源。

### 宿主终止通知保留独立出口

`agents/loop_driver.py::_send_termination_reply` 检查执行 scope 后调用 `EffectExecutor.send_mail`，后者仅委托 MemberMailService，并用稳定 request_id 去重。这是宿主生成的终止/失败通知，不是模型或 DSL 可组合业务操作；它没有普通操作的 invocation 回执和写 gate。

因此，`EffectExecutor` 不是应整类删除的旧业务执行器，但“所有外部效果均经 OperationExecutor”的说法不准确。P7 的统一授权暂停后，通知与未来授权/只读策略的关系仍待单独决策，不能宣称已经通过统一闸验收。

### 当前固定集合与未来授权

会话根通过 `MemberRuntime._assemble_session` 从 root-local executor 创建 OperationTool，冻结所用复合定义；`ToolExecutor.get_tool_definitions` 返回已注册定义。没有实现每轮依业务状态改写的 ToolView，也没有新的成员级统一 allowlist。现有服务装配和领域检查继续承担各自限制；固定工具列表本身不新增权限保证。

## 可复现检查

在仓库根执行以下命令。第一组预期无匹配（rg 返回码 1 表示未匹配）；其余检查需阅读匹配的上下文，区分历史记录、拒绝旧格式测试与现存 API。

```bash
rg -n 'class Tool\b|_HOST_QUERIES|ProgramEffects|MemberCapabilities|CONTROL_NAMES|ENDING_TOOLS|TRIGGER_BUILTINS|LOOP_BUILTINS|STAGE_COND_BUILTINS|iteration-limit-reached\?|error-limit-reached\?' src/tlon src/tlosure
rg -n 'read_only|staging/\{name\}/approve|version|input-schema|definition-id' src/tlosure src/tlon/agents/capabilities src/tlon_server/api/rest src/tests/agents/capabilities src/tests/server/test_rest_admin_capabilities.py
rg -n 'ScriptHostQueries|sleeping\?|running\?|lazy\?|member-roster|_send_termination_reply|class EffectExecutor' src/tlon/agents
rg -n 'class OperationTool|class ToolExecutor|class ToolResult|def control_names|def evaluate_condition' src/tlon
uv run python scripts/gen_reference_docs.py --check
bash scripts/check_all.sh
```

历史 ADR 中出现旧名是决策沿革；应标明现行决策覆盖，不能据此认定旧实现还在。反之，现行手册与注释误述已删除的 on-complete 或旧模型工具名，属于本次文档收口应修正的事实欠账，不能以历史为由保留误导。


## 源码注释欠账与构建身份

loop_driver.py 顶部和 _apply_outcome 的历史注释仍笼统称外部效果经 EffectExecutor；实际业务 Tool 走共享 Operation，EffectExecutor 负责受限宿主通知。本次现行文档已按代码修正。该源码注释保留为后续有运行时代码变更时同步清理的欠账，不据此误判旧业务执行器仍运行。

runtime_identity.py::build_identity 对 tlon/tlosure 的 Python 文件原始字节计算身份，改注释也会改变恢复构建身份。本次仅改 docs，不改 Python 源码、schema、配置或持久化格式；不会因本 PR 的源码变化引发在途根构建失配。不承诺不同部署环境或依赖变化仍可恢复。
