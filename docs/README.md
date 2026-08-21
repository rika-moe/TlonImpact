# Tlon 文档地图

语言：全中文；代码标识符、EDN 关键字、API 字段名保持原文。

## 分层原则

| 层 | 回答 | 变化频率 | 更新触发 |
|---|---|---|---|
| [vision.md](./vision.md) | 为什么做 | 慢 | 方向讨论拍板后 |
| [architecture.md](./architecture.md) | 系统骨架是什么 | 慢 | 分层/装配/通信结构变化 |
| [design/](./design) | 各域为什么这样设计 | 中 | 对应域的设计决策变化 |
| [reference/](./reference) | 怎么写/怎么配/怎么调 | 快 | 代码变化时**必须同步** |
| [guides/](./guides) | 怎么做某件事 | 中 | 对应流程变化 |
| [decisions/](./decisions) | 当初为什么这样定（ADR） | 只增不改 | 新决策拍板；废止用删除线或新 ADR 覆盖 |

规则：

- 引用代码时写**文件路径 + 符号名**（如 [agents/member_spec.py](../src/tlon/agents/member_spec.py) 的 `MemberSpec`），不写行号——行号会随修改漂移。
- reference 类文档未来会进 prompt，按精确、自包含的标准写。
- 不写未实现的规划进 reference；规划只放 [vision.md](./vision.md) 和对应 design 文档的"方向"小节。
- 文档与代码同步是验收标准的一部分（见根目录 AGENTS.md）。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [vision.md](./vision.md) | 愿景、哲学、路线 | ✅ |
| [architecture.md](./architecture.md) | 分层、依赖方向、装配、运行时数据流、存储布局 | ✅ |
| [design/dsl.md](./design/dsl.md) | DSL 设计哲学：为何 EDN、为何受限、机制/策略分离 | ✅ |
| [design/runtime.md](./design/runtime.md) | MemberRuntime、认知循环、stage、effects-as-data | ✅ |
| [design/mail.md](./design/mail.md) | Republic of Letters：投递、线程、唤醒、人类接入 | ✅ |
| [design/memory.md](./design/memory.md) | 双层记忆与 Summarizer | ✅ |
| [design/prototypes.md](./design/prototypes.md) | 原型库、spawn、信任分级 | ✅ |
| [design/unified-member-model.md](./design/unified-member-model.md) | **统一成员模型**（2026-08 拍板并实施阶段 2）：一类成员 + 世代更替，9 条决策与迁移路径 | ✅ |
| [design/server-protocol.md](./design/server-protocol.md) | REST + SSE 协议 v2 的设计取舍 | ✅ |
| [design/webui.md](./design/webui.md) | WebUI（apps/webui）：React+Vite、/ui/ 静态托管、cyberpunk 视觉规范 | ✅ |
| [design/deepseek-harness-review.md](./design/deepseek-harness-review.md) | 外部调研：dsh 对 Tlon 的可借鉴点（方向讨论输入材料，非决策） | ✅ |
| [reference/dsl-reference.md](./reference/dsl-reference.md) | EDN 语法、MemberSpec 全字段、builtin/stage/section 清单 | ✅ |
| [reference/config-reference.md](./reference/config-reference.md) | agent.yaml、环境变量、`~/.tlon` 目录布局 | ✅ |
| [reference/tools-reference.md](./reference/tools-reference.md) | 全部工具的参数与语义 | ✅ |
| [reference/api-reference.md](./reference/api-reference.md) | REST/SSE 端点与事件帧格式 | ✅ |
| [guides/quickstart.md](./guides/quickstart.md) | uv sync → dream → ops → 第一封信 | ✅ |
| [guides/writing-a-prototype.md](./guides/writing-a-prototype.md) | 手写一个 prototype 的完整走查 | ✅ |
| [decisions/](./decisions) | ADR：0001 EDN、0002 邮件唯一通道、0003 机制/策略分离、0004 stage 纯度、0005 SSE、**0006 统一成员模型** | ✅ |

✅ = 已写并与代码同步；📝 = 计划内未写。新增文档先登记进本表。