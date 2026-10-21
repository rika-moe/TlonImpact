# 可审批的复合 Operation

状态：EPIC A #184–#192 已合并；本页描述 EPIC B P5 的实现。P5 待独立 PR 验收合并，P6 纯条件准入与 P7 动态工具视图尚未实施。目标与阶段见 [统一操作](operations.md) 和 [实施计划](operations-plan.md)。

## 一个定义，两个入口

`capability.tlon` 保存复合 Operation 的源码与 JSON Schema。文件名及提案通道保留 Capability 术语，执行只有 OperationExecutor；不再注册另一套闭包能力执行器。Behavior 直接调用和 LLM Tool 投影使用同一输入、输出、效果与实现。

```clojure
{:version 2
 :name "thread-digest"
 :description "读取邮件并提取摘要"
 :input-schema {:type "object"
                :properties {:mail_id {:type "string"}}
                :required ["mail_id"]
                :additionalProperties false}
 :output-schema {:type "string"}
 :impl (fn [input]
         (let [mails (mail-search {:mail_id (get input :mail_id)})]
           (get (model-call {:prompt (str mails) :max_tokens 256}) :content)))}
```

impl 必须是固定一个参数的 fn；根输入必须是命名对象，输出使用同一 schema 支持子集。旧 version=1、args 位置参数与作者 pure 声明直接拒绝，无兼容读取、自动转换或别名。语言普通函数的多参数语法不受影响。

## 静态依赖与效果

编译器使用 tlosure 通用作用域分析接口，遍历所有可执行分支、局部 fn/defn、map/filter/reduce 与循环；quote 作为数据处理。只允许静态具名操作调用，未知函数目标、将参数当函数、通过计算选择操作均拒绝。局部函数递归仍受 fuel/depth 约束，注册操作不能作为值传递来隐藏依赖。

直接依赖拓扑编译，递归得到传递依赖指纹与 read/write 效果集合；空集合才是纯操作。缺失依赖、环、语言/宿主/复合名称碰撞明确拒绝。一个坏定义只影响自身和依赖它的闭包，其他定义可继续装配。实际成员服务缺失时，该闭包不会因静态目录中的名字存在而获准执行。

impl 只见 input、核心纯函数和依赖操作，不注入 self、msg、event 或成员运行环境。宿主身份由调用上下文传递，不能以作者身份代调用。

控制保护依赖 Operation.control 元数据，编译器拒绝引用控制操作的复合定义，包含间接依赖。finish-turn/sleep-until/exit-agent 的名字没有第二份保护名单；不同名称但 control=true 的宿主操作同样受保护。run-loop 保留为 Behavior 编排边界，不属于复合操作目录。

## 批准与重新批准

目录为 `~/.tlon/capabilities-staging/<name>/` 与 `~/.tlon/capabilities/<name>/`。artifact 的 name 必须与目录相同，meta.tlon 保留作者、提案说明与人工信任记录。批准前静态编译并验证当前依赖图，保存 definition-id 和 dependencies（完整传递依赖内容身份）。审批不执行真实宿主效果。

受信加载重新编译并核对批准绑定。直接修改已批准定义或任一依赖不会被自动接纳：自身或上层拒载，直到人类明确重新批准。新增无关能力不会使其他成员所有在途根失配。手写 artifact 也必须明确批准，不能仅靠放入可信目录获得执行信任。

管理 API 的批准入口为 `POST /admin/capabilities/{name}/approve`，支持从 staging 提升，以及对现有 trusted 定义重新批准；旧 staging 路径不保留。WebUI 展示输入/输出 schema、推导效果、依赖指纹、不可用原因和重新批准动作。批准不是成员执行授权，实际领域权限与装置约束仍在宿主边界检查，统一授权名单和动态视图属于 P7。

## Examples 的安全边界

```clojure
:examples [{:input {:value "sample"} :expect "sample"}]
```

examples 是附加审查材料，使用共享编译器、schema 与 fuel 求值。纯 examples 真正计算；效果 examples 必须显式提供 fake-results，例如 `{"mail-send" [receipt]}` 的逐叶结果队列。隔离执行器只有 fake 宿主，缺失队列、输出不合约或有未消费结果都失败；详情、提案、审批不得调用真实邮箱、Shell、模型服务或其他写端口。示例通过不构成执行授权。

## 执行、冻结与恢复

复合执行继承调用者根预算、取消/截止与成员写 gate；嵌套复合共享语言 fuel/depth，不为每层重置。子调用 ID 由父路径和确定序号派生，完成顺序不改变身份。叶子 invocation 是外部效果的恢复权威；复合不创建会阻断叶子重放的非幂等父 intent。完整完成后才保存派生成功/输出错误缓存，历史成功先于当前预算、取消与截止检查。

ProgramStore 原子保存程序源码、输入与所需复合闭包快照。会话在首次模型请求前冻结当时静态暴露的复合集合。root_definitions 保存源码/schema 及传递依赖身份，恢复从原快照编译，不从当前磁盘重新拼旧程序。宿主构建与宿主契约变化仍拒绝恢复；添加无关能力不会改变宿主 build_id。

删除或修改能力文件后，已有根仍用旧快照；新工作只能使用当前已批准定义。若当前 Behavior 因缺依赖无法准入，但成员存在冻结待恢复工作，可明确进入“仅恢复”激活：只恢复旧根，不接受当前 boot/邮件/idle 等新规则，诊断说明 Behavior 不可用，修正后需要显式重载。没有待恢复事实时仍严格拒载。坏根隔离继续遵守 [调用恢复](invocation-recovery.md)，不删除历史、预留或用户文件。
