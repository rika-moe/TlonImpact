# ADR 0032：SQLite session journal——事件与生命周期元数据同一事实存储

日期：2026-10-17。状态：已实施。

修订关系：取代 ADR 0007 中「每个 session 一个 JSONL 文件」的物理存储决策；session 事件溯源、append-only、replay、副作用不重演及「model-visible ⇒ journaled」不变式不变。ADR 0017 的 journal 真相层和 `last_distilled_at` 时间戳 watermark 语义不变。

## 背景

JSONL 实现把完成状态藏在每个事件文件内部。`list_completed`、`latest_completed` 和 boot 的 `find_unfinished` 必须扫描目录并全文解析日志；蒸馏在 reap 点先做一次完成集合扫描，再回读已完成日志以应用 watermark，生命周期累计读取为 O(N²)。thread-recap 与演武场轮询复用这些扫描路径。

仅增加派生索引仍会引入「journal 已提交、索引尚未更新」的跨存储崩溃窗口。既然替换存储契约，事件和生命周期元数据应进入同一事务性事实存储，而不是维护两个需要互相修复的真相副本。

## 决策

### 每个成员一个 SQLite 数据库

运行时 session 事实存于 `~/.tlon/{agent_id}/sessions.sqlite3`，由显式注入的 `TlonPaths` 解析。数据库按成员隔离，不复用邮局数据库，也不建立全局共享 session DB。

核心表：

- `sessions(session_id PRIMARY KEY, started_at, ended_at, end_reason, last_seq)`：一行一个 session；`started_at` 在首个 `session/start` 提交时写入，`ended_at/end_reason` 在 `session/end` 提交时写入。
- `events(session_id, seq, ts, type, event_json, PRIMARY KEY(session_id, seq))`：一行一个事件，`event_json` 保存完整事件，按 `(session_id, seq)` 重放。

索引覆盖完成时间、开始时间与未完成恢复查询。`ended_at IS NOT NULL` 表示完成；`started_at IS NOT NULL AND ended_at IS NULL` 表示可恢复的未完成 session。只有 stray 事件而没有 start 的 session 不参与 boot 恢复，沿用原语义。

### append 是一个事务

每次 append 在一个 `BEGIN IMMEDIATE` 事务中推进 `last_seq`、插入事件，并在 start/end 时更新对应生命周期元数据。提交成功后 `SessionJournal.append` 才返回，调用方随后才能修改内存中的 `AgentSession`；失败抛异常且事件、序号和元数据一起回滚。

SQLite 使用 WAL、busy timeout 和 `synchronous=FULL`。异步业务经工作线程调用同步 `sqlite3`，沿用 ADR 0030 的既有模式，不新增数据库依赖。进程内同一数据库的 append 由共享异步锁排序，数据库事务负责连接间互斥。

事件仍只追加，不提供 update/delete 事件 API。SQLite 取代 JSONL 后，不再有半行 JSON、逐行跳过坏行或首次 append 全文件计数 `seq` 的恢复模型。

### 查询先过滤元数据，再读取必要事件

- `list_completed`、`count_completed`、`latest_completed`、`find_unfinished`、`end_reason` 只查 `sessions`，不读取事件正文。
- 完成 ID 列表与 `COUNT(*)` 计数共用同一 `started_at` watermark 过滤；没有可用 `started_at` 的完成 session 始终 pending，沿用 ADR 0017 的安全方向。MemoryManager 蒸馏只对过滤后的 pending session 调用 `read_events`，digest 与演武场等待只要数量时只调用 `count_completed`。
- `read_events` / `resume` 只查询并重放一个 session 的事件，原有 context、tool collect、孤儿 dispatch 自愈和完成状态重放规则不变。
- 第一版继续采用 `last_distilled_at` 时间戳 watermark，不同时改成 completion sequence，避免把存储替换与蒸馏语义变化耦合。

### 旧 JSONL 是离线档案，不参与新运行时

旧 `~/.tlon/{agent_id}/sessions/*.jsonl` 原样保留；新 SQLite **不导入、不双写、不读取 fallback**。任何 session id 都只查询新数据库，不因数据库缺失或 session 不存在而搜索旧文件。

因此升级前的未完成 session 不由新运行时恢复，旧完成 session 不参与新的 thread-recap，旧 claims 的 `source_session_ids` 不能经新的 `readSession` 回读；旧文件本身不删除。既有 `memory/distillation.json` 保留，避免旧 session 因新数据库为空而重新蒸馏。

### 演武场导出物不是运行时 fallback

演武场归档从 SQLite 导出每个 session 的 `sessions/{session_id}.jsonl`，供现有报告生成器和证据指针使用。导出文件是可移植报告原料，不是运行时事实存储；运行时和归档过程均不读取成员旧 `sessions/` 目录作为输入。

### 存储治理边界

每成员单数据库消除了随 session 数增长的扁平文件目录和目录扫描，但不等于磁盘保留治理。本决策不自动删除、压缩或分片历史事件；冷数据导出与保留期限如需治理另立决策。

## 实施与验收

1. `TlonPaths` 增加 session 数据库路径；新增同步 SQLite store，`SessionJournal` 保留现有异步 replay 外观并接入事务存储。
2. MemoryManager 在读事件前应用 watermark；context recap、boot recovery、scheduler、reflection、retirement 和 readSession 经同一接口工作。
3. 演武场等待条件改用元数据查询，归档从 SQLite 导出报告 JSONL；报告格式与证据指针不变。
4. 删除只针对 JSONL 半行/文件路径/mtime 的测试，替换为事务原子性、并发 seq、SQLite 重启恢复和旧 JSONL 隔离测试。
5. 常驻回归必须证明：完成后的空 reap 不读取历史事件；thread-recap 只读取最新 session；boot 不读取已完成 session；append 失败不会留下半个事件或错误完成状态。
6. 更新 ADR 0007/0017 及 runtime、memory、arena、architecture 和工具参考中的物理存储描述，运行 `scripts/check_all.sh` 全量质量门并做隔离真实冒烟。

## 验证记录（2026-10-17）

- `scripts/check_all.sh`：14/14 全绿；Python 单元 2058 项、集成 16 项通过。集成包含真实 `dream` 子进程 + SDK 投信及 SQLite 收件箱验证。
- 相关 18 个测试文件的独立复核回归 282 项通过；后续 count refinement 回归 170 项通过，改动文件 ruff check / format check 通过。
- 隔离 `TLON_HOME` 真实 SQLite 冒烟：append、重建 Journal 后 replay、完成/未完成/latest/count/watermark 查询和演武场 JSONL 导出通过；预置旧 `sessions/legacy.jsonl` 未被读取、改写或导出，临时目录自动清理。未触碰用户现有 `~/.tlon`，未调用真实 LLM 或外部服务。
