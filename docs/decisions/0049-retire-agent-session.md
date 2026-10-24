# ADR 0049：退役 Agent Session，统一 Program Run

日期：2026-10-24
状态：已接受

Agent 的执行、恢复、历史、记忆来源与派生额度统一以 program run 为边界。turn 仅为 run 内的局部行动循环，不新增持久化 session 实体。保留 serving 层 client session（连接、身份及 SSE），不得混改客户端协议。

删除旧 Agent Session 历史读取链路、SessionStore、sessions.sqlite3 路径及过时超时配置。运行时不读取、迁移或兼容旧 session 历史，也不删除用户磁盘归档。旧记忆的 session 来源不转换、不提供回读兼容。

历史查询只读取 ProgramStore：RunJournal 提供完成/未完成清单、原始 run_id 查询和导出，读取事件使用 run/start、run/end 与 run_id，不再生成 session 事件或 program: 前缀。run-read 工具替代 session-read，参数及结果使用 run_id。ProgramStore 仍是唯一执行和调用回执权威，不复制账本。

记忆投影、蒸馏、反思、摘要、上下文回顾与来源字段统一 RunProjection、source_run_ids 及 run 术语。派生额度键为 (spawner_id, run_id)，session_spawn_quota 改为 run_spawn_quota；账本不转换旧 session 记录。旧 agent session 配置、工具、接口及测试直接删除，不保留别名或 fallback。

成员忙碌状态统一 is_busy，涵盖工作程序及后台维护；不得把维护活动冒充 run 数量。排空/休眠仍等待全部工作结束。宿主模型请求的执行关联使用 run_id，不改 client session 的 session_id。

验证覆盖 run 历史查询及导出、失败/未决记录、记忆来源回读、跨重启额度和恢复、排空与 client session 协议。完整质量门和隔离 dream + SDK 冒烟通过后统一提交一个 PR；不创建 issue。
