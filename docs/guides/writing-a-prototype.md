# 编写原型：Agent 作者入口

本文是 Agent 从需求到完整 prototype 的统一入口。原型是无人格的行为包：包含 v2 `behavior.tlon` 和完整 `modules/` 源码，描述怎样行动；模型、预算与成员身份由部署配置确定。人格资料不随行为包提交。机器工具 `program-check` / `prototype-write` 要求具体程序，不接受 `:parameters` 或待替换的模板占位符；人工管理 API 的模板能力不适用于机器提案。

## 先读什么、从哪里读

使用普通 `file-read`、`file-list`、`file-search` 读取以下 workspace 资料。路径相对当前 `working_dir`；默认绝对路径以 `/workspace/` 开头。例如 `file-read` 参数是 `{"file_path":"/workspace/tlon/docs/guides/writing-a-prototype.md"}`。这两个资料目录只读，L0 无 Docker 也可访问；把草稿放在自己的可写 workspace，不能改资料映射。

| 要解决的问题 | workspace 路径 |
|---|---|
| 作者流程、完整示例与提案 | `tlon/docs/guides/writing-a-prototype.md`（本文） |
| EDN、函数、集合、控制流、fuel | `tlon/docs/reference/tlosure-reference.md` |
| 事件输入、入口返回、模型与工具编排、恢复 | `tlon/docs/guides/writing-a-module-program.md` |
| Behavior 与宿主词汇 | `tlon/docs/reference/dsl-reference.md` |
| 工具目录与参数参考（以当前工具定义为准） | `tlon/docs/reference/tools-reference.md` |
| 实际标准库实现 | `tlon/stdlib/`，先 list 再按需 read |

`tlosure` 提供语言与纯计算内置函数；`tlon.*` 模块是宿主标准库；`mail-send`、`model-call` 等是宿主操作。标准库可用 `(:require [tlon.messages :as messages])` 等形式导入，但不要把它复制进 `modules`，也不能定义或覆盖 `tlon.*`。可读源码包括 `messages.tlon`、`conversation.tlon`、`workflows.tlon`、`context.tlon`、`authoring.tlon`。按需求选择，简单程序不必调用模型。

## 完整的最小双模块原型

需求：每封来信触发一次处理，向实际发件人回复 `收到：` 加原文，并把回信正文保存为本次入口结果。这个示例只需要邮件操作，不调用模型，也不需要容器。两个模块用于演示复用；单模块程序同样合法。

`behavior.tlon`：

```clojure
{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 10000 :execution {:level :L0}}
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

(defn reply [content]
  (str "收到：" content))
```

`handlers` 指向已导出的单参数函数。路径与 namespace 对应；提交 `modules` 的键相对模块根，所以是 `work/reply.tlon`，不是 `modules/work/reply.tlon`。所有依赖都必须随包提交或属于宿主标准库；不能只交修改片段。

邮件事件提供 `event.mail` 与 `event.sender`；不要把正文中的地址当成可信发件人。这里 `mail-send` 的目标取自事件。入口返回 `{:result value}`，value 必须可持久化为严格 JSON；无 `:next` 表示本次事件处理完成，之后仍可处理新事件。`:fuel` 是语言求值预算，模型额度与执行权限另由宿主控制；源码无法自行授予权限。邮件操作返回 accepted 仅证明本地投递接受，不证明收件人阅读或处理。

## 校验、修订、提交

1. 先把需求明确为触发事件、允许的效果和可观察结果。选择对应 handler，按工具契约编写完整源码。
2. 调用 `program-check`，参数如下（JSON 中的 `\n` 是源码换行）：

```json
{
  "behavior": "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 10000 :execution {:level :L0}}",
  "modules": {
    "work/reply.tlon": "(ns work.reply\n  (:require [work.text :as text])\n  (:export handle))\n\n(defn handle [event]\n  (let [answer (text/reply (get (get event :mail) :content))]\n    (mail-send {:to [(get event :sender)] :content answer})\n    {:result answer}))\n",
    "work/text.tlon": "(ns work.text (:export reply))\n\n(defn reply [content]\n  (str \"收到：\" content))\n"
  }
}
```

3. 检查 `valid` 与 `diagnostics`。失败时根据诊断修改完整包并重试；`valid: true` 与 `digest` 只表示静态准入通过，不证明能在预算内成功完成、业务正确或部署成员具有所需权限。校验不会执行、发布或批准程序。
4. 需要提交提案时，把通过检查的同一份完整源码交给 `prototype-write`。下列参数可以直接用于工具调用：

```json
{
  "display_name": "原文确认回信",
  "description": "收到邮件后向发件人回复收到及原文。",
  "behavior": "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 10000 :execution {:level :L0}}",
  "modules": {
    "work/reply.tlon": "(ns work.reply\n  (:require [work.text :as text])\n  (:export handle))\n\n(defn handle [event]\n  (let [answer (text/reply (get (get event :mail) :content))]\n    (mail-send {:to [(get event :sender)] :content answer})\n    {:result answer}))\n",
    "work/text.tlon": "(ns work.text (:export reply))\n\n(defn reply [content]\n  (str \"收到：\" content))\n"
  }
}
```

服务端生成原型 `name`，不要自行传 `name`。派生时可加 `parent`（现有原型系统 ID）与 `proposal_note`；先用 `prototype-read` 读取父包，修改后仍提交全部模块。`lessons` 是可选经验文本，不是人格文件。提交会再次校验，成功返回 `status: "pending_approval"`；这不表示获批或已经创建成员。

`tlon.authoring/draft` 是可选的普通标准库函数：它提示模型读本文与标准库，循环调用 `program-check` 修正，并返回合法的 `behavior/modules` 候选。它不发布、不批准；直接使用工具也遵守同一流程。

## 审批与运行验收

人类通过 WebUI / 管理端读取完整源码、依赖与谱系差异后批准，再创建成员并选择模型、预算与执行装置。Agent 的同伴评审不会替代人类批准；不能把 staging 提案直接当成可实例化的可信原型。已批准源码的修改必须提交新包，不能原地改已批准目录。

批准并部署后，在隔离测试实例向新成员发送包含唯一标记的邮件，检查该成员入口运行成功，并从收件人持久化邮箱确认确实收到 `收到：` 加原文。这才验证了事件、模块链接、宿主效果和实际投递的端到端结果。对于调用模型或更多工具的程序，还要覆盖实际任务的成功结果及预算、权限、异常处理；静态检查成功不能代替这些证据。

报告应分别说明：静态检查结果、提案 ID 与审批状态、运行测试输入、运行状态和实际收件箱证据。未获批或尚未运行时明确标注，不能声称原型已可用。
