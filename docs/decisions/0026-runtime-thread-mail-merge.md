# ADR 0026：运行时同线程来信合并——对齐 boot scan 语义到实时投递路径

状态：已接受（2026-10-07 拍板；EPIC #124）

## 背景

一封邮件 = 一个 session，而每个 session 的锚点重建 + 多轮循环平均烧 246k prompt tokens（2026-10-06 ~ 10-07 ledger：130 个 think session，平均 5.7 轮）。近 2 天 226 封收件中 94% 是 agent 互发，突发密集（CEO 59 封/2 天；存在 16 秒内向同一线程连发 5 封的实例）——邮件风暴期间大量 session 是为同一线轮的连续来信各自启动的。

合并语义其实早已存在，但只在扫描路径生效：boot 与后台 rescan 的 `_scan_unread_mail` 里，同线程未读邮件只把最新一封入队为代表、更早的兄弟标 read（内容靠 session 上下文的 mail-history 兜底）。而 EventBus 实时投递路径（`_on_event` → `_enqueue_cognitive` → `SessionScheduler.enqueue`）不做任何合并——这是两条投递路径的语义不对称，实时路径恰是风暴期间的主路径。

## 决策

1. **实时路径对齐扫描路径语义**：`_enqueue_cognitive` 入队前查询调度器待发队列，同一线程已有待发邮件时不再新建 session——旧邮件标 read，最新一封作为代表入队（内容与 boot scan 的 `_merged_thread_ids` 完全一致）。
2. **不 retroactive 合并**：已**运行中**的同线程 session 不受影响——它已在处理，新到的同线程来信照常入队（它会成为下一个 session 的触发信，或再被后续来信合并）。
3. **门控语义不变**：`:when` 门仍对每封来信逐封求值后再进入合并判断；队列满、requeue、并发上限（`max_concurrent_sessions`）语义均不变。
4. **被合并邮件的可见性**：与 boot scan 相同，靠代表信 session 的 mail-history（含 ADR 0025 两段式）兜底——内容不丢失，只是不单独触发一轮处理。

## 否决的备选

- **debounce（到信后等待 N 秒再启动 session）**：能捕获「session 运行中到达的同线程来信」，但给所有来信引入固定延迟，且延迟时长是无依据的策略参数；队列合并零延迟成本，先观察其效果。
- **机制层乒乓环路抑制**（线程深度限制 / per-sender 频率限制）：直接砍 agent 间通信，策略风险大、误伤正常协作；本决策只合并「尚无人处理的冗余触发」，不动任何已开始的处理。数据观察后再议。

## 后果

- 邮件风暴期间同线程连续来信只产生一个 session，直接减少锚点重建与循环轮次；对缓存的间接收益是并发 session 数下降、DeepSeek 缓存驻留概率提高。
- 两条投递路径的合并语义对齐，`build_thread_recap` 所描述的「同线程未读合并为一个 session」不再只是 boot scan 的特性。
- `docs/design/mail.md` 同步补运行时合并语义。
- 观测面：合并事件需有日志（被合并邮件 id、代表邮件 id、thread_id），否则「信到了但没起 session」无法排查。
