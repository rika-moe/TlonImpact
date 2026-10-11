# ADR 0028：收件箱驱动的单 session 模型

状态：已接受（2026-10-11 拍板）

## 背景

「一封邮件 = 一个 session」的 push 模型有三个结构性代价：

1. **token 成本**：每个 session 的锚点重建 + 多轮循环平均烧 246k prompt tokens（ADR 0026 引用的 2026-10 上旬 ledger 数据）；邮件突发时大量 session 为连续来信各自启动，ADR 0026 的同线程合并只缓解了同线程场景。
2. **缓存效率**：触发内容把邮件本体注入 prompt，每封邮件的触发语都不同，跨 session 前缀缓存只能命中触发语之前的部分。
3. **机制复杂度**：有界队列、并发上限、ADR 0026 队列合并、boot scan 合并、rescan 兜底——一整套机器只为回答一个问题：「这封信该不该现在开一个 session」。

根本性的重新 framing：agent 不是「每封邮件唤醒一次的一次性处理器」，而是「一个守着自己收件箱的常驻进程」——像人查邮箱一样。mailbox 本来就是 durable queue（既有设计注释语），本决策让它成为**字面上唯一的队列**。

## 决策

1. **单 session 制**。一个认知成员同一时刻至多一个存活 session。邮件送达时：无存活 session → 唤醒；有存活 session → 什么都不做——邮件躺在收件箱里，agent 下次调邮箱工具时自然看到。运行中的 session 的 prompt 不注入、不变更（缓存前缀绝对稳定）。

2. **触发内容不含邮件本体**。session 触发语是固定模板「你有 N 封未读邮件」（N = 唤醒时刻的可唤醒未读数）。N 逐 session 变化是可接受的——它位于触发消息内，前缀缓存命中其前的全部内容。邮件本体经工具结果进入上下文（append-only，对前缀缓存友好）。

3. **已读归 agent 显式管理**。新增两个邮箱工具：`check_inbox`（返回未读列表，id/from/subject/thread/content 一次给全，省二次调用）与 `mark_read`（接受 id 列表，批量标读）。agent「看到了不管、不标读」是**合法行为**——不接入弱模型，不设 completion gate 强制处理。`silence`、`send_email`（带 in_reply_to/thread_id）不变。

4. **三个唤醒点**：
   - 邮件送达且无存活 session；
   - session 收割时责任集之外出现新未读（链式唤醒，见 5）；
   - boot 与慢速周期 rescan（复用现有 rescan 基础设施，间隔拉长到分钟级）。

5. **责任集与热循环防护**。session 启动时快照当时的可唤醒未读 id 集合（责任集），**持久化进 journal**（崩溃恢复后链式唤醒判定不丢）。收割时检查未读：出现责任集**之外的新 id** → 立即开下一个 session；只剩责任集内的遗留 id（agent 看过选择搁置）→ **不**立即重醒，交给慢速 rescan。无此防护，决策 1+3（显式标读 + 允许搁置）与链式唤醒组合出死循环：搁置的邮件反复唤醒 session，烧 token 不产出。

6. **`:when` gate 保留在唤醒层**，纯确定性语义不变：来信先过 gate——拒绝的邮件系统直接标读（D6.1 延续，不计入可唤醒未读）；通过的计入可唤醒未读，参与唤醒判定与责任集快照。

7. **终止/失败回复锚点迁移（修订 ADR 0027）**。触发邮件不复存在，回复锚点改为责任集：异常终止（超时 / error_limit / 硬顶）时，给责任集内**每个人类发件人**发一封失败通知（按发件人去重，一人一封，固定模板，不走 LLM）；责任集内的 agent 发件人维持 ADR 0027 语义——只记 journal，不发信。人类判定沿用 `is_human_sender` 闭包。

8. **session 身份**。`session_id_for(member, mail.id)` 退役；每个 session 用 uuid 标识。ADR 0007 的 journal 机制不变：恢复时扫到未闭合（无 `session/end`）的 journal 即恢复该 session，责任集从 journal 读回。

9. **退役清单**。有界邮件队列、ADR 0026 队列合并（运行时与 boot scan 两侧）、`max_concurrent_sessions`、`message_queue_size` 全部删除。ADR 0026 标记为被本决策取代。

## 否决的备选

- **drain-merge（积压时合批，讨论中的 B 方案）**：保留了队列与 per-mail session 身份，只缓解积压场景；收件箱驱动把它作为特例吞掉（积压 = 收件箱里有多封未读），且一并解决了触发语破坏缓存的问题。
- **debounce（到信后等待 N 秒再唤醒）**：ADR 0026 已否决——给所有来信引入固定延迟，且窗口长度是无依据的策略参数。
- **session 常驻直到收件箱排空**（不结束、继续追加新未读为后续轮次）：上下文无限增长，失去了 session 边界这个天然的上下文重置点；收割重开每次有一次首调成本，已明确接受。
- **系统自动标读（read = seen，agent 读到即已读）**：剥夺 agent 的搁置权，「看过暂不处理」与「已处理完」无法区分，慢速 rescan 的自愈路径也随之消失。
- **completion gate（session 想结束时仍有未读则拒绝终止）**：与「允许看不到也不管」的决策冲突；不接入弱模型，不靠机制强迫勤勉。

## 后果

- scheduler 塌缩为一句「无存活 session 且可唤醒未读 > 0 → 开一个」；boot scan、实时投递、rescan 三条路径统一为同一唤醒条件。
- 跨 session 前缀缓存稳定性最大化：触发语短小且结构固定，邮件内容全部走 tool result。
- 行为风险（显性接受）：agent 不查收件箱、看了不回、不标读，都是合法行为。排查手段是 ledger / journal / `check_inbox` 调用痕迹，不是机制强制。
- 观测面：新增日志——唤醒（含责任集大小）、链式唤醒（新邮件 id 列表）、遗留未读交 rescan（遗留 id 列表）、失败回复发送对象列表。「信到了但没起 session」的排查路径：先看是否有存活 session，再看责任集与链式唤醒日志。
- ADR 0026 废止（被本决策取代）；ADR 0027 的锚点定义被第 7 条修订（抑制语义本身保留）；ADR 0007 的 session 身份来源变更；ADR 0025 两段式 mail-history 不受影响（仍是 ambient context）。
- 人类成员不受影响（无 session 模型）；WebUI/observer 视图不变（mailbox 文件格式不变）。
