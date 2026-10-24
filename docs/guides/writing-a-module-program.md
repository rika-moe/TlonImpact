# 编写模块工作程序

P1 已提供人工完整模块包、事件入口和持久执行。P2 已提供完整模型请求与纯消息标准库；P3 提供受控工具分派和普通对话函数，P4a 已提供 Agent 完整模块提案和标准工作函数；P5 已将全部内置原型切换为 v2 模块程序。设计见 [可编程工作流程](../design/programmable-workflows.md)。

## 文件与入口

每个包有 `behavior.tlon` 和 `modules/`。例如：

```clojure
{:version 2
 :handlers {:mail-delivered work.reply/handle}
 :fuel 1000
 :execution {:level :L0}}
```

`modules/work/reply.tlon`：

```clojure
(ns work.reply
  (:require [work.text :as text])
  (:export handle))

(defn handle [event]
  (let [answer (text/reply (get (get event :mail) :content))]
    (mail-send {:to [(get event :sender)] :content answer})
    {:result answer}))
```

`modules/work/text.tlon`：

```clojure
(ns work.text (:export reply))
(def prefix "收到：")
(defn reply [content] (str prefix content))
```

路径必须与 namespace 对应；只允许显式 require 的导出函数，其他定义私有。模块顶层只接受 defn 和字面常量 def，加载不执行函数体或宿主效果。入口必须固定一个参数。闭包、高阶函数和递归使用普通求值器，所有函数共享该次入口的 fuel/depth 与根预算。缺依赖、依赖环、私有访问、重复绑定、越界路径或 symlink 明确拒绝。

事件包含原事件字段、`payload` 和冻结的 `self`；邮件事件另外提供物化的 `mail` 与 `sender`。`:boot` 入口的 type 为 `"boot"`，不隐式注入 msg/self 到模块函数的词法环境。

入口必须返回 `{:result value}`，可选 `:next`。result 是可持久化的严格 JSON 值（map 可用 Keyword 键）；不可返回函数、Symbol、NaN 等。结束子函数不会调度 Agent。

```clojure
{:result "done" :next {:kind "sleep-until" :at "2026-10-22T09:00:00+08:00"}}
{:result "done" :next {:kind "exit"}}
```

next.kind 也接受对应 Keyword；新提交的 sleep-until 时间必须在未来并带时区，沿用现有宿主调度约束。只有最外层入口结果会被解释，非法 next 或缺 result 将运行标为失败，不回退到旧引擎。无 next 时仅结束本次事件处理。

## 人工发布与审查

使用既有本地浏览器来源边界调用管理 API：

- `POST /admin/prototypes/staging`：提交 `{display_name, behavior, modules}`，modules 是 `{"work/reply.tlon": "源码", ...}`，路径相对 modules 根；整包先校验再原子发布。
- `GET /admin/prototypes/staging/{name}`：查看行为和 modules 源码，WebUI 原型详情也只读展示模块。
- `POST /admin/prototypes/staging/{name}/approve`：验证当前完整包并批准，摘要绑定精确行为与全部模块内容。
- `POST /admin/prototypes`：人工直接创建受信完整包，同样先校验并绑定内容摘要。
- `POST /admin/config/agents`：按既有 prototype/name/model 格式创建成员，复制完整模块包；装置权限仍来自实例配置。

v2 包源码发布后不可 PATCH，staging 也一样。修改请创建新包并用 parent 记录来源；元数据编辑不构成源码重新批准。直接修改已批准目录的源码会导致实例化拒绝。写侧模板允许 behavior 声明 `:parameters`，并在模块源码中使用占位符；实例化先渲染模块并移除声明。运行时只接收具体 v2 handlers 包，拒绝残留 parameters、rules、recipe 字段。

## 执行与恢复

首次受理在同一事务保存入口、完整源码包、事件输入、fuel 与 token 约束。后续磁盘修改或删除不改变旧运行。程序从冻结源码重放，已有宿主回执返回旧结果，未知非幂等效果不自动重做。

ProgramStore 的 entry_programs 保存结果与调度意图，运行完成与意图原子提交；调度器以 run_id 持久接纳去重。跨存储采用幂等交付，不声称跨数据库事务。旧睡眠已醒来或被管理员替代后，重放不会重建它。所有程序只通过模块入口执行；旧格式拒绝，不兼容或失败回退。

模块入口通过 ProgramStore 保存交互，RunJournal 提供只读统一视图，供反思、蒸馏、回顾与退休清点消费。恢复受现有宿主构建身份约束，不支持换版本宿主重放。

## 模型请求与纯消息标准库（P2）

宿主显式提供 `tlon.messages`，作者可导入 `user`、`assistant`、`append`：

```clojure
(ns work.writing (:require [tlon.messages :as m]) (:export generate))
(defn generate [task]
  (model-call {:messages [(m/user task)] :tools [] :max_tokens 128}))
```

`user` 和 `assistant` 构造包含 role/content 的消息 map，`append` 返回追加一个消息的新列表；这些函数没有宿主效果。`tlon.*` 是保留命名空间，作者不能覆盖。宿主标准源码只在链接与运行冻结时加入，不写进作者的 modules、管理 API 返回值或审批作者源码。

`model-call` 必填 `messages`，可选正整数 `max_tokens`（省略时使用冻结成员配置的单次输出上限 `llm.max_tokens`）；`tools` 是工具名称列表，省略等同 `[]`。消息 role 可用 user、assistant、tool，作者不能提供 system 消息或任意工具 schema。工具目录由成员装配提供；模型只生成工具建议，工具执行由显式 tool-execute 编排端口负责。

返回值含 `content`（工具专用响应可为 nil）、`tool_calls`、`finish_reason`、`usage`、`reasoning_content`。每条 tool_call 是 `{:id "..." :name "..." :arguments {...}}`；usage 保留 provider 的 snake_case 键。不要将 nil content 当成模型调用失败。完整示例见 [生成—核验—修订回信程序](../../src/tlon/resources/examples/reviewed-reply/README.md)，三个普通函数共享同一根预算（成员 `program_token_budget`，默认 100000，与单次输出上限分开）。

v2 受理时原子冻结宿主系统提示词、模型配置及工具定义；恢复沿用冻结配置。新模型请求校验当前工具目录，已提交结果允许按原工具契约重放。如果模型响应与额度已提交，但外层 Operation 回执尚未提交，恢复从同一 ProgramStore 模型记录补回执，不调用 provider、不重复扣额。已派发但结果未提交仍是 unknown，不能自动重发。output 模式按获准输出上限预留且不退款，total 模式沿用实际 usage 结算和缺失用量时的保守预留。

输入历史中的 assistant `tool_calls` 使用 provider 消息结构：`{:id "..." :type "function" :function {:name "..." :arguments "JSON字符串"}}`，tool 消息用 `tool_call_id` 关联。它与输出的规范化 `{id,name,arguments}` 不同，P2 不自动把建议执行成消息历史；P3 的 tlon.messages 和 tlon.conversation 完成协议拼接。

## 工具分派与普通对话（P3）

```clojure
(ns work.dialogue (:require [tlon.conversation :as conversation]) (:export ask))
(defn ask [task]
  (conversation/run {:messages [{:role "user" :content (str task " Use tools for actions; call finish-turn alone when done.")}]
                     :tools ["time-now" "finish-turn"] :max_rounds 4}))
```

对话函数返回局部结果，调用方可继续核验、修订或发送邮件。它不创建隐式 session，也不重置根预算、取消信号或语言 fuel。`max_rounds` 是程序的轮数上限；行动循环必须成功执行独立且空参数的 `finish-turn` 才返回，而不是终止整个事件入口。纯文本、无工具调用、截断或达到轮数上限明确失败，不自动发信。发信必须使用 `mail-send` / `mail-forward`，成功投信不结束循环；规划、自评和压缩可以经 `context/complete` 返回文本。

需要自定义控制顺序时，可直接组合 model-call 与编排端口：

```clojure
(tool-execute {:tools ["time-now"]
               :tool_calls [{:id "clock-1" :name "time-now" :arguments {}}]})
```

工具名只能来自该次请求的范围和成员目录。批次先拒绝重复 ID、非法名称和范围外调用；叶子按输入顺序执行并返回结果。每个叶子有稳定身份与独立回执，批次没有遮蔽部分成功的父 intent。恢复已完成项返回历史回执，尚未执行项检查当前工具定义与权限；遇到未知结果阻塞运行。

只有输入/领域拒绝可转换为模型可修正的工具失败。取消、截止、预算、恢复失配、未知效果和基础设施异常传播给运行器。工具结果按原 ID 关联完整 assistant 响应，不能把部分成功拼成缺少 tool 结果的新模型请求。

完整示例见 [工具辅助核验回信](../../src/tlon/resources/examples/tool-reviewed-reply/README.md)：子对话读取时钟后返回，外层继续核验、修订和发信。内置原型均为 v2 模块程序；旧 session 仅保留只读历史。

## Agent 起草、审阅和派生（P4a）

作者资料直接映射到 workspace：`tlon/docs/` 是当前文档，`tlon/stdlib/` 是实际标准库源码。用现有 file-read、file-list、file-search 查询；相对路径按当前 working_dir 解析，默认绝对路径为 `/workspace/tlon/...`。资料只读，L0 无 Docker 时也可读取；L0/L1/L2 均可用五个普通文件操作读写自己的持久 workspace，无需 Docker；不能借资料映射越界访问宿主或其他成员。联系簿为工作目录下的 `CONTACTS.md`，模型按需读取，使用 file-edit/file-write 更新，不全文注入系统提示。Docker 可用时同路径也是只读挂载。wheel 带入同一份仓库文档，不维护第二份内容副本。

起草是普通 `tlon.authoring/draft` 函数：传入 messages、max_tokens、max_rounds，可按需显式选择 tools。默认工具是文件读取/列举/搜索与 program-check。提示仅提供入口路径，模型自行读取资料；没有宿主预读手册注入、专用一次性起草调用或隐藏候选缓存。模型通过 program-check 的结构化参数提交 behavior/modules，得到诊断后继续修订；draft 返回通过检查的候选，不负责发布。单模块程序也合法，两个可复用模块是验收场景而非强制门槛。

program-check 是只读 Operation，复用发布时的完整包校验。返回 valid、digest、diagnostics；坏源码是普通校验报告，基础设施异常继续传播。它不执行候选，不证明终止、业务正确或拥有执行权限。`prototype-write` 最后仍重新验证完整 v2 包并提交 staging，不能只交模块 diff；不保留 behavior-propose 别名或旧机器起草入口。

人工审批仍是必要步骤：在 WebUI 或原型详情读取全部源码、谱系每个文件的新增/删除/修改与依赖变更，再批准并创建成员。同伴 prototype-review 也读取完整作者包，评审不代替人类批准。审批 digest 绑定完整作者内容，改源码创建新包；已有实例与旧运行使用各自复制/冻结的源码。宿主标准库随运行冻结，不夹进作者审批内容。

## 标准方法、上下文与压缩（P4a）

`tlon.workflows` 导出 short-turn、plan-then-act、evidence-check、reviewed、route、forward。前四项接收普通 conversation options；route 的 method 选择短回合、计划执行、独立证据核验或评审修订，方法本身仍是可嵌套调用的函数。forward 接收目标地址和原 mail_id，经 mail-forward 转发原信。完整两模块示例见 [standard-work](../../src/tlon/resources/examples/standard-work/README.md)。

`tlon.context/assemble` 组合 prefix/history/task；`fit` 根据宿主 model-context 的完整请求估算决定是否压缩，`complete` 组合 fit 与 model-call。估算包括冻结系统提示与选定工具，返回 input_tokens、context_window、output_tokens、fits，不调用 provider、不消费模型额度；估算不能保证 provider 的最终 token 计数。

`compact` 保留 cache_prefix_len（也作为默认 prefix_count）和最近 recent_groups 个完整交换，逐组累积摘要。每次摘要请求先核查窗口；单个交换无法容纳时明确拒绝，不拆坏工具协议。摘要区分未执行草稿/建议、真实工具结果、失败与不确定性，不冒充新执行回执。所有摘要、计划、核验、修订共享原根预算与持久模型回执；前缀不得切断 assistant/tool 交换或缩短缓存区。

v2 运行的责任邮件、原错误与模型草稿保留。终止或不安全 unknown 隔离时，宿主向已核实的原人类发件人投递稳定、幂等失败通知；通知使用独立零模型额度维护根，不赠送原程序额度，不向其他 Agent 发送自动失败信。通知故障不覆盖原错误，可恢复的安全 unknown 仍按原调用键对账，取消仍传播。新交互历史已接入统一只读视图；stage 引擎与 v1 执行路径已删除。

## 回读交互来源（P4b）

模块入口的完成状态与模型/工具回执保存在 ProgramStore。记忆或 digest 中的 `run_id` 可直接传给现有 `run-read`，读取有界摘要与来源；旧 agent session 历史不再读取。读取不会恢复执行，也不会消费模型额度。thread recap、蒸馏、反思、教训及退休清点读取同一视图。来源尚未蒸馏也可读取，claim 中的来源引用不代表另一个维护消费者已经处理完成。

内置 `tlon.agent/respond` 的 `max_rounds` 是工具循环上界，同时受入口共享 fuel 和模型额度约束。达到上限会明确失败，保留未读原邮件与执行回执，按宿主失败通知规则处理；不把用尽轮数当作成功完成。

当前历史边界以 [ADR 0049](../decisions/0049-retire-agent-session.md) 为准：agent session 已退役，run-read/run_id/source_run_ids 为唯一程序历史协议；不提供旧历史兼容或迁移，client session 保留。
