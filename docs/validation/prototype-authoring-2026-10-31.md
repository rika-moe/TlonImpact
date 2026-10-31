# Agent 原型作者入口验收（2026-10-31）

本次验证 [统一作者指南](../guides/writing-a-prototype.md) 能支持真实模型读取资料、生成完整模块包并运行。验收使用 `deepseek/deepseek-chat`，没有 mock 模型，也没有向模型提供候选源码。所有 dream 服务均使用临时 TLON_HOME 和随机本地端口；真实 ~/.tlon 仅只读获取 provider 凭据，没有修改成员或配置。

## 任务与流程

通过 HTTP 向作者成员发需求邮件：生成至少两个自定义模块，将来信原文转换为 `VERIFIED[` + 原文 + `]END`，向事件发件人回信，并返回转换后的正文。子代不得调用模型或其他无关工具。

作者 harness 用普通 `tlon.authoring/draft` 运行真实模型工具会话，候选通过检查后由 harness 调用 `prototype-write`；这次验证不是普通行动循环自主选择提交时机的测试。共发生 7 次真实模型调用、8 次 file-read、2 次 file-list、1 次 file-search。读取材料包括统一作者入口、语言参考、模块指南、DSL 参考、工具参考和 messages 标准库源码。一次 program-check 即返回 valid=true、diagnostics=[]。staging 提案 ID 为 `p-404acda1874f459b9dc677d2d676f44a`，状态 pending_approval。

主会话检查完整源码：两模块导入导出匹配，唯一外部效果为向 event.sender 调用 mail-send，变换只使用 str。随后在隔离测试实例批准并创建子代，用 HTTP 投递输入，直接查询持久化邮箱与 ProgramStore 验证。

## 真实模型生成的作者包

behavior.tlon：

```clojure
{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 10000 :execution {:level :L0}}
```

modules/work/reply.tlon：

```clojure
(ns work.reply
  (:require [work.wrap :as wrap])
  (:export handle))

(defn handle [event]
  (let [mail (get event :mail {})
        content (get mail :content "")
        answer (wrap/verify content)]
    (mail-send {:to [(get event :sender)] :content answer})
    {:result answer}))
```

modules/work/wrap.tlon：

```clojure
(ns work.wrap (:export verify))

;; Pure text transform: VERIFIED[ + content + ]END, no padding, no rewriting.
(defn verify [content]
  (str "VERIFIED[" content "]END"))
```

## 运行结果

| 输入 | 持久化回信与入口结果 |
|---|---|
| `hello-真实验收-7319` | `VERIFIED[hello-真实验收-7319]END` |
| `line 1\n第二行 [] !`（实际换行） | `VERIFIED[line 1\n第二行 [] !]END`（保留实际换行） |

两次 run 均 completed；作者模块与冻结源码逐项一致，宿主自动加入 tlon/messages.tlon。子代调用记录只有两次 mail-send，没有模型调用。最终复验实例 health 已不可访问，临时 home 已删除；没有占用 8080。

## 验收脚本修正与证据边界

首次作者 harness 误设 fuel=500000，在任何模型调用前被正确拒绝，修正为上限 10000。真实生成并运行后，两条收件箱断言通过，但脚本将冻结模块字典与作者包全等比较，忽略宿主自动加入的 messages 模块，导致验收断言失败。随后使用同一份未修改的模型产物在新隔离实例复验，没有再次调用模型。复验还修正了结果读取断言：result_edn 保存的是入口 :result 的值，而非整个返回 map。最终核对两次完成状态、持久化回信、入口结果、冻结源码及清理全部通过。

因此证据覆盖真实生成、校验、staging、审查批准和子代运行；最终完整 SQL 证据来自对同一模型产物的独立复验。此单一确定性回信案例不代表复杂模型工作流或任意原型的成功率。

## 质量门

主会话执行 scripts/check_all.sh：12/12 全绿。Python 单元测试 2473 passed、130 skipped；集成测试 55 passed、1 skipped；WebUI 测试 116 passed，构建通过。新增文档示例测试直接提取指南中的 JSON，验证提交与校验包一致、静态准入和实际模块求值回信。跳过项遵循现有平台与环境条件。
