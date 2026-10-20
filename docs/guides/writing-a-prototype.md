# 编写行为原型

原型是可审查、可复用的成员程序模板。人格、模型和联系人由创建成员时分别配置，不写进原型；背景见 [成员模型](../design/unified-member-model.md) 与 [原型设计](../design/prototypes.md)。

## 1. 原型的文件

每个原型目录包含 `prototype.tlon` 元数据与 `behavior.tlon` 行为文件。技术 ID 由系统生成，显示名描述行动习惯，如“短回合交流”“证据核验”；不要用职业或历史人物名称代替行为习惯。

行为统一由规则组成：`:on` 订阅事件、`:when` 纯条件、`:actions` 普通 DSL 程序，另有可选求值预算 `:fuel`。同一成员可在条件、函数和传统循环内产生模型会话、邮件投递或其他效果，不按是否使用模型分类。

## 2. 编写一个短回合回复程序

```clojure
{:version 1
 :execution {:level :L0}
 :rules
 [{:name "respond"
   :on :mail-delivered
   :when (or (addressed-to-me? msg) (cc-ed? msg))
   :actions
   [(run-loop
     (quote
      {:context {:sections [(persona) (protocol) (lessons) (skills)
                            (knowledge) (current-task) (mail-history 20 10)
                            (thread-recap)]}
       :stages [(think)
                (critique {:if (sending-mail?)})
                (act {:if (or (not (sending-mail?)) (not (critique-failed?)))})
                (revise {:if (critique-failed?)})]
       :until (or (iteration-limit-reached?) (error-limit-reached?)
                  (token-limit-reached?))
       :max-iterations 3
       :max-consecutive-errors 3
       :max-tokens 32000
       :reflect {:enabled false :interval-minutes 15}}))]}
  {:name "boot" :on :boot :actions []}]}
```

`run-loop` 请求一次按配方运行的会话。`quote` 保留 `(think)` 等表达式为数据，避免在外围规则求值时提前执行它们。配方中的上下文、阶段、终止条件、三项资源上限和反思配置都需显式声明，字段见 [DSL reference](../reference/dsl-reference.md)。

评审失败时，`act` 不派发草稿，`revise` 将完整草稿及“尚未发送”状态交给下一轮。是否标读邮件由程序或模型显式决定：会话完成、`finishTurn` 或等待不会代替 `mail-mark-read`。

同一个程序还可以使用 `if`、`let`、`while`、`map`、`defn`，不需要为不同分支创建不同类别的规则。配方可以位于条件分支或函数中；同一成员允许多条规则请求不同配方，也允许非邮件事件请求会话。

本阶段效果先被收集、再按顺序执行，模型返回值暂不能在普通表达式中绑定并继续判断。需要这种能力的完整工作流属于后续设计。

## 3. 一个直接转发的程序

```clojure
{:version 1
 :execution {:level :L0}
 :parameters {:to {:type :string-list :required true}}
 :rules
 [{:name "relay"
   :on :mail-delivered
   :when (or (addressed-to-me? msg) (cc-ed? msg))
   :actions [(mail-forward {:mail_id (mail-field msg :id) :to ${to}}) (mail-mark-read {:mail_ids [(mail-field msg :id)]})]}
  {:name "boot" :on :boot :actions []}]}
```

`${to}` 是写侧模板参数，实例化前渲染为 EDN 地址向量。`mail-forward` 显式指定来源邮件 ID，`mail-mark-read` 显式指定标读 ID；它们与 `run-loop` 一样是普通动作。会话之后也能转发同一封原邮件，不存在专属规则类型。

想循环通知多个地址时可用 `(map address (mail-send {:to [address] :content "通知"}) recipients)`；传统循环本身无需请求模型。效果收集器会保留嵌套产生的全部动作。

## 4. 人格、创建与提案

原型不携带 IDENTITY、SOUL、RULES 或 CONTACTS。创建成员时播种人格与联系人；IDENTITY 描述身份背景，SOUL 描述价值与思考倾向，RULES 描述约束和运行约定，具体边界见 [identity](../design/identity.md)。

人类可在 WebUI 选择受信原型、填写参数、选择模型并创建成员。成员使用 `spawnAgent` 指定原型、名字和可选参数，子成员继承创建者的模型部署配置。新成员与手建成员运行同构；是否开机装配由 `:on :boot` 规则决定，不是人格或职业决定。

成员撰写新原型走 `writePrototype` → staging → 评审 → 人类批准 → 实例化。原型不能授予额外宿主权限。L0/L1/L2 执行装置等级和社会信任政策仍约束工具注册与实际执行，见 [原型设计](../design/prototypes.md)。

## 5. 本地校验

先解析并通过唯一加载校验器，避免把格式问题留到实例化或运行时：

```python
from pathlib import Path
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlosure.edn import loads

source = Path("behavior.tlon").read_text()
spec = MemberSpec.from_edn(loads(source))
validate_member_spec(spec)
print("OK", len(spec.rules))
```

模板文件须先通过模板渲染再进行成员实例校验。字面 `run-loop` 配方会校验 section、stage 和终止条件，动态配方在请求执行时再次验证。旧规则顶层 `:loop` 已删除，加载直接拒绝，不提供兼容转换。

检查重点：

- EDN 的 map、vector 和 form 闭合正确；`quote` 恰有一个参数。
- 规则有名称和事件；门控只调用纯词汇，动作只引用作用域内符号。
- 会话配方包含必填项与有限预算，阶段条件使用对应词汇。
- 转发保留真实来源，发送失败依据回执处理，草稿不视作已发送。
- 显式决定何时标读；四态请求与邮件投递相互独立。
- 显示名描述行动习惯，模型与人格不写入原型。
