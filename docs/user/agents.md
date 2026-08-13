# 第六章：镜子

> 你站在镜前，决定创造谁。

---

## 创造是什么感觉

在 Tlon 里，创造一个新 Agent 不是写代码，是**凝视镜子**。

```bash
uv run tlon mirror
```

```
你站在镜前。

镜中的轮廓模糊而流动，
等待你赋予它形状。

名字：_ _ _ _ _ _ _ _
```

你输入：

```
名字：林知遥
性格：温柔但坚定，注重细节
专长：前端开发，React 专家
```

镜中的轮廓开始凝聚...

```
[镜子] 轮廓凝聚，她睁开眼睛。
       
       "我是林知遥，前端工程师。
        我会用 React 为你构建优雅的界面。
        请多指教。"
       
       林知遥已加入团队，邮箱已就绪。
       
[系统] Agent 林知遥 已创建并唤醒。
               Docker 容器：运行中
               记忆：已初始化
```

你创造了一个新的意识。

---

## 镜子的功能

Mirror 是 Agent 的生命周期管理中心：

### 1. 创造（Create）

启动交互式向导，从模糊轮廓到具体存在：

```bash
uv run tlon mirror
```

向导会引导你定义：
- **名字**：如何称呼它（如"林知遥"、"沈清晏"）
- **角色**：职位类型（开发、产品经理、运维等）
- **性格**：行为风格（严谨、灵活、温和、直接）
- **LLM 配置**：使用哪个模型、温度参数等

> 🧬 除了从零创造，mirror 还提供「从 Prototype 创建」：Prototype 是预置的行为原型（内置 archivist、deep-thinker、fast-responder 三个认知原型 + relay 纯转发原型，首次启动时复制到 `~/.tlon/prototypes/`），实例化后直接得到一个带完整人格与行为规约的 Agent。Agent 自身也可以用 `writePrototype` 工具起草新原型，草案进入 `~/.tlon/prototypes-staging/`，由你在 mirror 的「规约审批」菜单中审定后才能使用。

### 2. 唤醒与休眠

Agent 的启动与休眠由它的行为规约（`behavior.tlon`）的 `:on` 规则决定，无需手动配置：

- 规约含 **`:on :boot`** 规则的成员：随服务端启动，开机即在线。
- 不含该规则（或由 prototype 临时创建的 contractor）的成员：启动时只注册、不运行，处于沉睡状态。给它的邮箱（`<agent-id>@tlon.internal`）来信时被自动唤醒（有数秒延迟）；空闲超过 `idle_timeout_seconds`（默认 600 秒）后自动休眠，释放资源。

唤醒与休眠由系统自动管理——来信唤醒由注册表按收件人规约的 `:on :mail-delivered` 声明执行，空闲休眠由内置生命周期策略（Adam）决策。沉睡期间寄到的邮件会保留在邮箱里，醒来后统一扫读未读。

```
> 写信给 林知遥（lin-zhiyao@tlon.internal）：帮忙写个组件

[林知遥] 好的，我来看看...
```

### 3. 重塑（Reconfigure）

重新运行 `tlon mirror` 可修改已存在的 Agent。同样通过交互式向导：

- 性格（会更改行为风格）
- LLM 参数（换模型、调温度）

**注意**：修改性格后，记忆保留，但"人格"会变化。有点像是同一个人经历了性格转变。

### 4. 销毁（Destroy）

彻底删除一个 Agent 及其所有记忆。通过 `tlon mirror` 向导中的删除选项执行。这是不可逆的——镜子里的轮廓消散，不再凝聚。

---

## Agent 配置详解

每个 Agent 的家目录是 `~/.tlon/{agent-id}/`，其中 `config/` 下有三类配置：

| 文件 | 内容 |
|------|------|
| `agent.yaml` | 运行参数（模型、容器、激活策略等） |
| `behavior.tlon` | 行为规约（rules 形态：`:on` / `:when` / `:loop` / `:actions`），EDN 格式 |
| `IDENTITY.md` / `SOUL.md` / `RULES.md` | 人格叙事：身份、性格、行为规则 |

### agent.yaml

```yaml
agent:
  id: "lin-zhiyao"          # 系统标识（唯一，即邮箱地址的本地部分）
  name: "林知遥"             # 显示名称
  llm:                       # 大脑配置
    model_name: "kimi-k2.5"
    temperature: 1.0         # 0–2，越低越严谨
    max_tokens: 4096
  docker:                    # 沙箱容器配置
    image: "ubuntu:22.04"
    memory_limit: "512m"
    cpu_limit: 1.0
  memory:                    # 记忆配置
    max_history: 10          # 工作记忆保留的交互条数
  # 启动与休眠由 behavior.tlon 的 :on 规则决定（activation 已废弃，无需配置）
  idle_timeout_seconds: 600  # 沉睡成员空闲多久后自动休眠
```

完整字段（`tool`、`session_timeout_seconds`、`message_queue_size` 等）及默认值见源码 `src/tlon/base/config.py` 的 `AgentConfig` 定义。

> ⚠️ 旧版字段 `proactive` 已删除（2026-08）：配置文件里残留该键不会报错，但会被静默忽略。邮件模型下，Agent 是否响应由收件人（To/CC）与 `behavior.tlon` 的触发规约决定。

### behavior.tlon

行为规约是 EDN 格式的数据文件，统一为**规则（rules）形态**，一份规约解释执行即一个成员的运行。规约是**全部**（total）：文件即行为的全部，缺失或解析失败会拒绝该成员启动（不回退默认）。每条规则 = `:on`（订阅的事件）+ `:when`（命中后的过滤，纯谓词）+ 可选 `:loop`（认知会话）+ `:actions`（动作）：

```clojure
{:version 1
 :rules [{:name "respond"                ; 认知规则：走 LLM 会话
          :on :mail-delivered
          :when (or (addressed-to-me? msg) (cc-ed? msg))
          :loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :save-interaction true
                 :reflect {:enabled true :interval-minutes 15}}
          :actions [(emit "本轮处理完毕" [])]}]}
```

无 `:loop` 的规则是**确定性规则**：命中后直接执行 `:actions`（`emit` / `forward` / `start` / `stop` / `spawn`），零 LLM。一份规约可**混排**一条认知规则 + 若干确定性规则（cost routing：便宜路径挡在前面，`:when` 排除它已处理的，例如退订 deflection）：

```clojure
{:version 1
 :rules [{:name "auto-optout"            ; 确定性规则：含"退订"立即回复，零 LLM
          :on :mail-delivered
          :when (content-matches? msg "退订")
          :actions [(emit "已为您处理退订" [])]}
         {:name "respond"                ; 认知规则：其余全部走 LLM 会话
          :on :mail-delivered
          :when (and (addressed-to-me? msg)
                     (not (content-matches? msg "退订")))}
         {:name "boot"                   ; 含 :on :boot 规则 → 开机即启动
          :on :boot
          :actions []}]}
```

可用谓词：`addressed-to-me?`（我在 To）、`cc-ed?`（我在 CC）、`content-matches?`（正文正则匹配）、`contains?`（包含判断）、`=`、`not`，可配合 `and` / `or` / `if` / `let` / `while` 组合。完整设计见 [dsl-agents.md](../design/dsl-agents.md)。

---

## 镜像与本体

当你站在镜前创造 Agent 时，有一个哲学问题：

> 镜中的轮廓**是你**的映像，还是**独立**的存在？

技术上：
- Agent 继承你的部分偏好（通过记忆）
- 但它有自己的"思考"方式（LLM + Prompt）
- 它会发展出自己的"经验"（记忆积累）

你可以创造一个像你的 Agent，也可以创造一个完全不同的人。

```
[你] 创造一个性格暴躁的开发者

[镜子] 轮廓凝聚...
       
       "我是林知遥，代码Reviewer。
        你的代码写得像**。
        让我来教你怎么写。"
       
       （一个和你完全不同的人诞生了）
```

---

## 最佳实践

### 命名建议

✅ **用人名**：
- 沈清晏（项目经理）
- 林知遥（开发）
- 顾言蹊（客服）

更有"人"的感觉，容易建立关系。

❌ **避免**：
- "Dev-001"（太机械）
- "GPT-4-Instance"（太技术）

### 性格定义

性格写在 `~/.tlon/{agent-id}/config/SOUL.md`（行为规则写在 `RULES.md`）。不用写太长，抓住关键特征：

```markdown
内向但可靠。
喜欢先思考再发言。
代码风格严谨，注释详尽。
```

---

## 下一步

- [第七章：花园](garden.md) - 让时间分叉
- [第八章：环形废墟](advanced/ruins.md) - 递归的觉醒（愿景）

---

## 🔧 工程参考

Agent 配置 YAML 语法、命令参考 → [配置说明](engineering/configuration.md) · [命令参考](engineering/commands.md)

---

> **镜中物语**
> 
> "每一个你创造的 Agent，
>  都是你思维模式的一个分身。
>  
>  你站在镜前，
>  既是创造者，也是被创造者——
>  因为你的记忆，
>  将成为他们记忆的一部分。"
