# 普通工作程序的退役代码清理

本次清理延续 [ADR 0046](../decisions/0046-programmable-workflows.md) 和 [P5 审计](workflow-p5-audit.md)：执行与恢复只保留普通模块程序。P5 已删除 stage 引擎；这里处理残留的写入 API、重复入口表示、独立宿主查询回执和失效观测链路。本文记录实现边界，不作为测试或冒烟结果报告。

## 当前执行链

```text
MemberSpec.handlers -> MemberHandler(on, entry)
  -> MemberRuntime -> ProgramDriver.run_entry
    -> DSLAdapter / OperationExecutor -> ProgramStore invocations
```

一个 handler 同时声明订阅事件和模块入口，不再把同一声明转换成 MemberRule/rules 再查回 handlers。入口事件数据由 entry_env 物化。既有程序身份仍使用 `<事件或邮件身份>:handler:<事件名>`；更换内部表示不改变邮件去重与当前程序身份。

注册表读取也是普通只读 Operation，使用同一输入校验、调用身份、采样回执和恢复边界：

| 调用 | 输入 | 返回 |
|---|---|---|
| `member-sleeping` | `{:agent_id "成员 id"}` | 是否具有显式休眠意图 |
| `member-running` | `{:agent_id "成员 id"}` | 是否在运行表中 |
| `member-lazy` | `{:agent_id "成员 id"}` | 是否允许空闲卸载 |
| `member-roster` | `{}` | 格式化的公开寻址黄页 |

例如 `(member-running {:agent_id "alice"})`、`(member-roster {})`。这些是外部读取，不能标记为纯计算；恢复返回已记录的采样值，不重新读取注册表。旧 `sleeping?`、`running?`、`lazy?` 和无对象参数的宿主 builtin 调用删除，不保留别名。`member-list` 返回结构化目录，`member-roster` 返回文本，两者仍共享邮局的公开寻址目录。

## 删除范围

- 删除仅服务旧规则运行的 ProgramStore.ensure、ensure_many、complete；当前入口使用 ensure_entry 与 complete_entry。
- 删除 SessionStore 的初始化、建表及 append 写入能力。历史数据库样本由测试辅助代码生成，生产代码不再为了测试夹具保留旧写入口。
- 删除 MemberRule/rules 重复投影与 runtime 的旧 rule 执行命名。
- 删除 ScriptHostQueries 注入和 ProgramDriver 的独立宿主 effects 读写路径；注册表读取由普通 Operation 记录。
- 删除 thinking 与无消费者的 tool.execute/tool.result 事件。仍有 UI 与 metrics 消费的模型、工具观测移至 ModelService 与 OperationExecutor 的真实调用边界，历史回放不重复计数。

通用 tlosure 的 EDN、求值器、函数、模块、燃料计费与静态校验仍是现行基础。policy、邮件库等各自的 version 1 与 MemberSpec v1 无关，不按数字批量删除。

## 历史只读与阻断保留

SessionStore 只读取已有历史，SessionJournal 汇合旧历史与新程序的只读投影。旧记录不转换成新程序，不 append、不 resume、不执行。保留历史读取是为了保留可查询和可导出的证据，不是保留旧执行器。

legacy_work_error 继续检查未完成旧 session、旧规则运行及无法确认的存储状态，并明确阻断。删除旧执行代码不能把未知工作自动解释为成功，也不能静默丢掉未完成效果。原始数据库不因本次清理被删除或清空。

## 存储边界

`program.sqlite3` 中 `runs.rule_edn` 的旧字段名仍保留。当前入口将其用作冻结的入口声明标记；实际模块包与恢复材料位于 package_edn。该字段不意味着规则解释器仍存在，也不用于重新启用 v1 执行。

本次不为命名清理改写已有 SQLite 布局，不引入迁移、兼容执行器或清库路径。旧 effects 表即使存在于已有数据库，也不再被当前执行链用于读取或写入回执；调用权威为 invocations。保留已有存储布局避免破坏当前冻结程序、调用证据及历史数据。跨宿主构建恢复仍遵守既有身份核验与明确拒绝策略。

当前接口清单由 reference generator 从代码生成；历史 ADR 和阶段审计保留当时证据，由本页说明当前覆盖边界。

## 集成测试子进程生命周期

#216 首次 Linux CI 在 `test_mail_smoke.py` 前无后续进度，最终被取消；旧日志没有 setup/call/teardown 阶段信息，不能据此确认该次事件的唯一根因。原始真实 CLI 在隔离 Linux 复跑能通过，但故障注入确定性复现了旧清理路径的缺陷：直接终止 launcher 后，后代仍持有 stdout 和监听端口；已开始的 `Process.wait()` 及后续输出 drain 可能一直等不到管道 EOF。仅查看 launcher.returncode 不能证明服务已经退出。

真实 CLI fixture 现在通过 `start_new_session=True` 拥有独立进程组，退出时向整组发送 TERM，沿用原来的 10 秒优雅退出预算，未完成则向整组发送 KILL，并有界等待进程回收和输出 EOF。正常退出、启动失败、测试异常及取消均进入清理路径，launcher 已退出时也不跳过后代清理。测试验证后代持有管道与端口、忽略 TERM 的升级清理，以及真实 dream 退出后端口不可连接。

健康检查和身份初始化共用原 45 秒启动预算，单次请求另有较短限制；不再让 aiohttp 默认 300 秒请求等待越过启动预算。CLI 邮件测试的请求也显式有界。失败日志记录 PID、启动/身份初始化/测试体阶段和服务输出，强制清理失败记录进程及管道状态。集成门逐项输出测试名及最慢阶段耗时，便于区分测试执行与 teardown 停滞。以上调整不增加总等待来掩盖挂点，也不修改生产服务的信号或恢复语义。

Linux 完整复核还发现退休邮局测试的独立 TOCTOU：先观察 active_session_count 为零，再写历史并发起立即退休，后台维护可在其间开始，触发 StopRejectedError。测试改用既有 when_idle 排空退休接口，并在同一原有等待预算内验证持久退休标记与运行表移除；不把一次空闲采样当作停止许可，不修改生产退休规则或延长等待。

修复验证：本地完整质量门 14/14，单元 2447 passed / 15 skipped、集成 44 passed（128.66 秒）；隔离 Linux aarch64、uv 0.13.5 的完整集成 44 passed（106.44 秒）。Linux 测试结束无 uv/tlon 活进程，验证容器已移除。原始真实 CLI 两项单独复跑能通过，故不将清理缺陷表述为首次 CI 事件已唯一确认的根因。
