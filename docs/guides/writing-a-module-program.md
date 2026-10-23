# 编写模块工作程序（P1–P3）

P1 已提供人工完整模块包、事件入口和持久执行。P2 已提供完整模型请求与纯消息标准库；P3 提供受控工具分派和普通对话函数，Agent 自动起草模块待 P4a；现有内置原型仍使用 v1 stage 引擎。设计见 [可编程工作流程](../design/programmable-workflows.md)。

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

v2 包源码发布后不可 PATCH，staging 也一样。修改请创建新包并用 parent 记录来源；元数据编辑不构成源码重新批准。直接修改已批准目录的源码会导致实例化拒绝。模块模板参数化不属于 P1；v2 不接受 v1 的 parameters/rules/recipe 字段。

## 执行与恢复

首次受理在同一事务保存入口、完整源码包、事件输入、fuel 与 token 约束。后续磁盘修改或删除不改变旧运行。程序从冻结源码重放，已有宿主回执返回旧结果，未知非幂等效果不自动重做。

ProgramStore 的 entry_programs 保存结果与调度意图，运行完成与意图原子提交；调度器以 run_id 持久接纳去重。跨存储采用幂等交付，不声称跨数据库事务。旧睡眠已醒来或被管理员替代后，重放不会重建它。新程序不启动 stage scheduler；新旧执行路径按明确格式选择，不失败回退。

当前模块程序不产生新会话的 SessionJournal 交互投影，不改变现有反思/蒸馏消费者；这些在 P4b 接入。恢复受现有宿主构建身份约束，不支持换版本宿主重放。

## 模型请求与纯消息标准库（P2）

宿主显式提供 `tlon.messages`，作者可导入 `user`、`assistant`、`append`：

```clojure
(ns work.writing (:require [tlon.messages :as m]) (:export generate))
(defn generate [task]
  (model-call {:messages [(m/user task)] :tools [] :max_tokens 128}))
```

`user` 和 `assistant` 构造包含 role/content 的消息 map，`append` 返回追加一个消息的新列表；这些函数没有宿主效果。`tlon.*` 是保留命名空间，作者不能覆盖。宿主标准源码只在链接与运行冻结时加入，不写进作者的 modules、管理 API 返回值或审批作者源码。

`model-call` 必填 `messages` 与正整数 `max_tokens`；`tools` 是工具名称列表，省略等同 `[]`。消息 role 可用 user、assistant、tool，作者不能提供 system 消息或任意工具 schema。工具目录由成员装配提供；模型只生成工具建议，工具执行由显式 tool-execute 编排端口负责。

返回值含 `content`（工具专用响应可为 nil）、`tool_calls`、`finish_reason`、`usage`、`reasoning_content`。每条 tool_call 是 `{:id "..." :name "..." :arguments {...}}`；usage 保留 provider 的 snake_case 键。不要将 nil content 当成模型调用失败。完整示例见 [生成—核验—修订回信程序](../../src/tlon/resources/examples/reviewed-reply/README.md)，三个普通函数共享同一根预算。

v2 受理时原子冻结宿主系统提示词、模型配置及工具定义；恢复沿用冻结配置。新模型请求校验当前工具目录，已提交结果允许按原工具契约重放。如果模型响应与额度已提交，但外层 Operation 回执尚未提交，恢复从同一 ProgramStore 模型记录补回执，不调用 provider、不重复扣额。已派发但结果未提交仍是 unknown，不能自动重发。output 模式按获准输出上限预留且不退款，total 模式沿用实际 usage 结算和缺失用量时的保守预留。

输入历史中的 assistant `tool_calls` 使用 provider 消息结构：`{:id "..." :type "function" :function {:name "..." :arguments "JSON字符串"}}`，tool 消息用 `tool_call_id` 关联。它与输出的规范化 `{id,name,arguments}` 不同，P2 不自动把建议执行成消息历史；P3 的 tlon.messages 和 tlon.conversation 完成协议拼接。

## 工具分派与普通对话（P3）

```clojure
(ns work.dialogue (:require [tlon.conversation :as conversation]) (:export ask))
(defn ask [task]
  (conversation/run {:messages [{:role "user" :content task}]
                     :tools ["time-now"] :max_tokens 128 :max_rounds 4}))
```

对话函数返回局部结果，调用方可继续核验、修订或发送邮件。它不创建隐式 session，也不重置根预算、取消信号或语言 fuel。`max_rounds` 是程序的轮数上限；模型无工具建议时返回，而不是终止整个事件入口。

需要自定义控制顺序时，可直接组合 model-call 与编排端口：

```clojure
(tool-execute {:tools ["time-now"]
               :tool_calls [{:id "clock-1" :name "time-now" :arguments {}}]})
```

工具名只能来自该次请求的范围和成员目录。批次先拒绝重复 ID、非法名称和控制请求；叶子按输入顺序执行并返回结果。每个叶子有稳定身份与独立回执，批次没有遮蔽部分成功的父 intent。恢复已完成项返回历史回执，尚未执行项检查当前工具定义与权限；遇到未知结果阻塞运行。

只有输入/领域拒绝可转换为模型可修正的工具失败。取消、截止、预算、恢复失配、未知效果和基础设施异常传播给运行器。工具结果按原 ID 关联完整 assistant 响应，不能把部分成功拼成缺少 tool 结果的新模型请求。

完整示例见 [工具辅助核验回信](../../src/tlon/resources/examples/tool-reviewed-reply/README.md)：子对话读取时钟后返回，外层继续核验、修订和发信。已有内置 v1 原型与交互消费者保留至后续阶段。
