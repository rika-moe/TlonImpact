# Execution Device 设计：能力阶梯与宪法执行层

> 本文是 agent 执行装置（execution device）的设计哲学与规划。当前实现只有 L2 的一层（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py) 的 `DockerRuntime`），本文把它放进一个更大的图景：device 不是「给 agent 一台电脑」，而是**书信共和国宪法的执行层、自进化自然选择的竞技场**。现状描述以代码为准，未实现的部分集中在「能力阶梯」「方向」两节。

## device 是什么：两个极端之间的空白

Tlon 已有的两个执行环境位于光谱的两端：

- **tlosure 求值器**（[tlosure/eval.py](../../src/tlosure/eval.py)）——被故意阉割到极点的执行环境：可停机、无副作用、fuel 计量、只能组合已有机制。它是最小 execution device，弱到只配做纯计算，但因此安全到可以交给 agent 自己书写。
- **Docker 容器**（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）——最大 unrestricted 的执行环境：图灵完备、任意 syscall（网络已默认断开，issue #23）。它是最强 execution device，强到 agent 在里面做什么系统都管不住。

问题是**两个极端之间空无一物**：一个只读信回信的成员和一个需要编译代码的成员，拿着同一台 full-power 的机器。device 的设计哲学，首先就是把这片空白组织成有秩序的阶梯。

再往下挖一层：在自进化的叙事里，device 扮演的角色是「环境」——DNA 是 `behavior.tlon`，突变是 agent 在 device 里写出的新程序，选择压是信任闸（静态校验、演武场、同行评审、人类审批），遗传是原型库。**device 是表型遇见环境的地方，是自然选择发生的竞技场。** 它的设计目标因此不是「能跑命令」，而是：让试错极其便宜、让失败极其无聊、让每次试验都能向全社会解释自己。

## 五条宪法

### 1. 能力阶梯，而不是一个房间

每个成员不该拿到同一台万能机器。执行能力与行为一样，是**声明在基因组里的数据**——「spec 是唯一权威」从行为延伸到执行面。成员声明自己需要哪一层，默认拿最低够用层；device 需求随原型提案一起被评审（一个 staging 原型声明 L2，本身就是评审时的红旗）。这是最小权限原则（POLA）的 Tlon 方言：能力即数据，授权即评审。

### 2. 可读性：device 必须对全社会可读

书信共和国的根基是一切皆可查证——邮件持久、可检索、可问责。device 里发生的事也不能是黑箱：每次 exec 进 journal/trace，workspace 的每处变更可 diff。推论很具体：**workspace 是 git 仓库**——agent 的每处改动是一个 commit，diff 就是一封可以邮寄、可以评审的「信」。agent 写的程序，可以用共和国自己的协议提交同行评审。

### 3. Ephemerality：试验要便宜，失败要无聊

正确的模型是把执行当成 tlosure 求值那样的东西：**输入 → 有界执行 → 输出，效果显式声明，其余蒸发**。持久的东西只有 workspace（bind mount 出来的那部分）。容器是 cattle 不是 pet——ADR 0006 说成员不死，但成员的「鞋」可以每穿一次就扔。

**已落地**（issue #25，混合方案 D'）：`run()` 每条命令创建一个一次性执行单元（同镜像/挂载/ro rootfs/tmpfs/限额/网络，跑完读输出即删），常驻容器降级为「无菌文件柜」——只服务文件 I/O，从不执行任何命令。详见「方向」P2。

### 4. 预算即数据，像 fuel 一样

tlosure 最漂亮的思想是 fuel：计算有预算，耗尽是优雅停机而不是事故。device 把这条推广到底：时间预算、内存/CPU 预算（已有 per 容器）、**社会级预算**（每成员每日 CPU-秒、磁盘总量——现在完全没有的暗账），全部声明在 config 里、可审计、可按成员调节。预算即数据，数据即可治理。

### 5. 突变晋升通道：device 的产出可以改写语言本身

agent 在 workspace 里写的程序，和 tlosure 的词汇表之间应该有一条晋升通道：

> workspace 里的一个程序（突变）→ 演武场反复验证（选择）→ 原型提案通道评审（信任闸）→ 成为 tlosure 的新 builtin（遗传）——从此全社会每个成员都能在自己的 spec 里组合它。

这条通路一旦成立，「行为即数据」就闭环了：agent 不光能改写自己的行为，还能**改写语言本身**——当然，永远经过信任闸。device 是突变的发生器，信任闸是选择器，DSL 词汇是基因库。三者咬合，自进化才不是口号。

## 抽象接口：ExecutionDevice

device 定义为一个以数据为参数的抽象接口，隔离级别、持久性、网络策略、预算全是参数：

```
run(program, inputs, budget) -> outcome
```

- `program`：要执行的东西（shell 字符串、程序文件路径——都是数据）
- `inputs`：stdin、环境、挂载声明
- `budget`：时间 / 内存 / CPU / 输出上限
- `outcome`：结构化结果——exit code、stdout/stderr、**文件系统变更 diff**、资源消耗计量

> **当前形态（P1，issue #21）**：接口已落地为 [sandbox/device.py](../../src/tlon/sandbox/device.py) 的 `ExecutionDevice`（`typing.Protocol` + `@runtime_checkable`），但签名是**刻意的最小化**——`run(program, timeout, workdir) -> ExecutionResult`（对齐 `DockerRuntime` 现状，outcome 即 `exit_code/stdout/stderr`），外加 `read_file` / `write_file` / `file_exists` / `close`。inputs/budget dataclass 与带资源计量的 outcome 推迟到第二个实现出现时再引入：现在只有一个实现，不做预防性抽象。

`DockerRuntime` 是这个接口的第一个实现（L2 层）。接口化的直接收益：能力声明可以进成员 config，装配层按声明选择实现与参数；exec 审计、资源账本挂在接口边界上，对所有实现一刀切。

## 能力阶梯

| 层 | 环境 | 适用 | 实现 | 状态 |
|---|---|---|---|---|
| L0 | tlosure 求值器 | 纯计算、数据变换 | 已有（[tlosure/eval.py](../../src/tlosure/eval.py)） | ✅ |
| L1 | 受限运行时 | 运行 agent 编写的程序，无 syscall 自由 | WASM/WASI（capability 安全模型：授权即数据，guest 默认什么都摸不到） | 调研中 |
| L2 | 完整 Linux userland | 真正需要真实机器的声明者（bash/gcc/python） | Docker（现 `DockerRuntime`）；gVisor/microVM 为升级位 | ✅（加固 #22 + 一次性执行单元 #25） |

L1 要诚实定位：WASI 里没有「shell」，agent 写的 bash 命令不能直接跑（coreutils 有 WASI 移植但工作流摩擦真实存在）。L1 是「运行程序」的层，不是 bash 的替代品。gVisor/Firecracker 是「需要更强隔离时」的升级位，不是现在的需求——别为了哲学换轮子。

## 现状评估：DockerRuntime 的优点与欠账

**做对了的**（详见 [architecture.md](../architecture.md)「存储布局」容器视角）：

- 纵深防御：自己家目录只读 bind-mount（内核级）+ 工具层 path_guard 白名单（字符串级），跨 agent 读取结构上不可能
- 生命周期与统一成员模型自洽：容器跨睡眠保留、唤醒零成本、从不强删（ADR 0006）
- 文件写入不依赖镜像内软件（put_archive 内存 tar）；阻塞 SDK 调用全部 `asyncio.to_thread`；双层超时（容器内 `timeout` 真杀 + 外层 `wait_for` 兜底）

**欠账**（按严重度，前两项已立案）：

1. ~~`network_mode: "host"` 默认——容器共享宿主机网络命名空间，agent 可直连服务端 REST（`/messages` 的 username 取自请求体）、内网与外网。「邮件是唯一通道」目前只靠工具层自觉，结构上是筛子。**违反宪法零**。~~ **已修复**（issue #23）：默认翻转为 `none`，见「方向」P1。
2. path_guard 白名单是字符串级，容器内 symlink 可逃逸（`/workspace/x -> /etc/passwd` 后 readFile 跟随）。写侧已被 ro rootfs 内核级封死（issue #22，见「方向」P1），读侧残留。
3. `read_file` 用 `cat` 经 stdout 回传，`decode(errors="replace")` 损坏二进制（issue #1）——写入用 put_archive、读取却不用 get_archive，不对称。
4. docker client 从不关闭，sleep→wake 重复装配泄漏连接；socket 探测失败完全静默（issue #4）。
5. 执行依赖镜像内 `bash` 与 `timeout`，与文件 I/O「不假设镜像有软件」的哲学自相矛盾；错误语义靠 stderr 字符串匹配（换 locale 即碎）。
6. ~~容器配置漂移无 reconcile~~ → 已解决（issue #22）：`ensure_running` 比对期望配置与实际配置，不符自动重建。
7. 只有数量闸（`max_activations=8`）没有资源闸——8×512m 是暗账（违反宪法四）。→ 暗账已变明账（issue #24）：实际占用进 metrics，`TLON_CONTAINER_MEMORY_BUDGET` 超预算记 warning；强制型资源闸仍是后续决策

## 方向

按「地基 → 宪法加固 → 阶梯成形」排序，每项对应 EPIC 下的 sub issue：

**P0 地基（bug 修复）**
- issue #1：`read_file` 改 get_archive，二进制安全
- issue #4：docker client 生命周期管理 + 探测失败日志

**P1 接口化与加固（全部落在现有实现上，不扩能力）**
- ✅ ExecutionDevice 抽象接口，`DockerRuntime` 改造为首个实现（本 PR，issue #21）：接口落 [sandbox/device.py](../../src/tlon/sandbox/device.py)（`ExecutionResult` 迁入、`DockerRuntime.execute` 改名 `run`）；exec 审计经接口边界进 trace/log——装配层用 `AuditedExecutionDevice` 包装，每次 `run` 发 `device.exec` span + `device_exec` structlog 日志（命令截断前 200 字符、耗时、退出码、timed_out；资源消耗计量随完整形态 budget/outcome 一起推迟）（宪法二）。**刻意的最小化**：完整形态 `run(program, inputs, budget)` 推迟到第二个实现出现时（见「抽象接口」加注）
- ✅ 容器加固（本 PR，issue #22）：rootfs 只读（`read_only=True`）+ `/workspace` rw + `/tlon-home` ro + tmpfs `/tmp`（64m）——把「可写面 = 工作区」变成内核事实；写侧 symlink 逃逸封死（经真 daemon 冒烟验证：put_archive 的 daemon 端 tar 解包**替换** symlink 为普通文件而不跟随，`/etc/passwd` 未被触及——机制不是「跟随后被 ro 拒」，而是「根本不跟随」）。配置漂移 reconcile 落地为 `ensure_running` 里的 `_container_mismatches` 比对（image / binds / mem_limit / cpu quota / network_mode / read_only+tmpfs 七维），不符记 `container_config_drift` 日志后 remove + 重建——workspace 在 bind-mount 上状态不丢，与 ADR 0006 不冲突。**行为变化**：加固前创建的存量容器在下次 wake 时被自动重建一次，这正是加固触达存量的机制（真 daemon 冒烟：改 mem_limit 触发重建、workspace 内容保留）
- ✅ 网络宪法（本 PR，issue #23）：**现状调查先行**——全部历史工具调用 154 条中 bash 仅 8 条、联网命令 0 条（7 条 sleep/echo 冒烟 + 1 条容器内 ls），7 个成员 workspace 全空，仓内原型 spec 与工具描述零网络引导——默认断网不破坏任何现有工作流（潜在影响仅是开发/运维角色尚未发生的预期场景）。据此 `network_mode` 默认 `"host"` → `"none"`（[base/config.py](../../src/tlon/base/config.py) 的 `DockerRuntimeConfig`）；**外联的口子保留**：成员在 agent.yaml 的 `docker.network_mode` 显式声明（如 `"bridge"`），声明即数据、随原型提案一起评审。存量容器经 #22 的漂移 reconcile 在下次 wake 自动重建为断网，无迁移负担。egress 代理是远期，等第一个真实外联需求，不做预防性抽象
- ✅ 社会级资源账本（本 PR，issue #24）：全局容器内存/CPU 实际占用进 metrics（宪法四）。`sandbox/resource_monitor.py` 的 `ContainerResourceMonitor` 每 30 秒枚举 `tlon-agent-*` 容器收集 `container.stats`（内存 usage/limit + docker stats 标准公式的 cpu_percent），经 `stats_snapshot()` 纯内存端口喂给 `MetricsService` 的 observable gauge（`tlon.container.memory_bytes`/`tlon.container.cpu_percent` 按 agent_id，`tlon.society.container_memory_bytes`/`tlon.society.containers` 社会级总量）——`GET /metrics` 的同步 snapshot 路径永不阻塞在 docker 调用上。fail-soft：daemon 不可达只记 warning、账本为空。docker client 探测链提取为 `sandbox/docker_client.py` 共用。**边界**：社会级内存预算 `TLON_CONTAINER_MEMORY_BUDGET`（如 "4g"）是 warning-only——超预算记 `container_memory_budget_exceeded` 日志，不做激活拒绝等强制（强制策略是后续决策）

**P2 阶梯成形（随真实需求驱动）**
- ✅ ephemeral 执行单元（本 PR，issue #25，混合方案 D'）：**无菌文件柜 + 一次性执行单元**。关键洞察：get_archive/put_archive 对 created-but-never-started 的容器同样有效（真 daemon 验证），所以常驻容器可以**从不启动**——降级为只服务文件 I/O 的「文件柜」，不执行就不会被污染（#22 的漂移 reconcile 继续守护它）。`run()` 不再 exec 进常驻容器，改为每条命令创建一次性容器（命名 `tlon-agent-{agent_id}-exec-{uuid8}`）：创建参数与文件柜同源于 `_container_kwargs`（同镜像/挂载/ro rootfs/tmpfs/限额/网络），`start → wait → logs 分路取 stdout/stderr → finally remove(force=True)`；不用 auto_remove（与 wait/logs 有竞态）。双层超时保持：容器内 `timeout` + 外层 `asyncio.wait_for`（+5s 兜底，触发时 finally 强杀容器）；超时文案契约（`Command timed out after {n} seconds`，AuditedExecutionDevice 依赖）不变。已知边界：busybox `timeout`（如 alpine 镜像）作为容器 PID 1 时不触发，此类镜像靠外层兜底限时（晚 ≤5s，语义不变）；默认 ubuntu 镜像（coreutils）走 124 路径。**行为变化（目的而非 bug）**：agent 自己 nohup 的进程不再跨命令存活，后台任务走 `background=true`（host 侧 asyncio task 包一次性容器，天然兼容）；唯一持久状态是 /workspace。资源账本（#24）同步适配：exec 单元按 `-exec-` 命名标记归属到所属成员聚合计量，避免每条命令产生一个随机 uuid 指标序列。工具契约（executeBashCommand description）已告知 LLM 新语义
- 能力声明进成员 spec/config，装配层按声明选择层级与参数（宪法一）
- L1 受限运行时调研：WASM/WASI capability 模型与 agent 工作流的匹配度
- workspace git 化：改动即 commit，diff 即评审材料（宪法二）

**P3 进化专属机制（对应愿景「闭环」阶段）**
- 演武场（proving ground）：staging 原型实例化临时成员，放进强化隔离容器（断网、时间盒、种子数据只读），喂合成邮件跑认知循环，评审看「它在围墙里做了什么」（完整 journal 轨迹）而不只是「它写了什么」——信任闸从静态审查升级为行为审查。演武场容器是试验装置不是成员，`auto_remove` 与 ADR 0006 不冲突
- 突变晋升通道：workspace 程序 → 演武场验证 → 原型提案 → tlosure 新 builtin（宪法五）
