# Execution Device：成员执行权限与容器机制

执行装置隔离成员的文件访问和代码执行。tlosure 求值、文件操作、Linux 命令执行分成 L0/L1/L2 三层，按最低够用原则授权。Capability 已删除，见 [ADR 0045](../decisions/0045-remove-capabilities.md)；行为评估见 [arena](arena.md)。

## 领域约束

- 运行时执行权限的权威是成员 `agent.yaml` 的 `docker.level`。原型 `behavior.tlon` 的 `:execution` 是实例化输入，不是每次运行重新授权的来源；成员自改 spec 不能提高运行权限。
- 没有代码执行权限的成员，装配层不注册 Shell 工具，装置层也拒绝 `run()`；权限不能只靠提示词表达。
- 文件柜不执行程序，每条 Shell 命令使用一次性执行单元。跨命令持久状态通过挂载的 workspace 保存。
- 容器只挂载自己的成员目录，家目录只读；代码执行不能获得其他成员目录或宿主 Docker socket。
- 对命令执行保留时间、CPU、内存和网络边界；可观测的资源账本与强制配额要区分。

## 层级与授权

| 层级 | 当前实现 | 可用能力 |
|---|---|---|
| L0 | 无 Docker 装置 | tlosure 计量求值及非装置工具；不注册文件、Shell 工具 |
| L1 | DockerRuntime 的 archive-only 文件柜 | 五个 filesystem 工具；无代码执行 |
| L2 | 文件柜 + 一次性执行单元 | filesystem 工具及 `shell-run` / `shell-check` |

[base/config.py](../../src/tlon/base/config.py) 的 `DockerRuntimeConfig` 默认 `level="L1"`。[agents/prototypes/instantiate.py](../../src/tlon/agents/prototypes/instantiate.py) 的 `instantiate` 把原型声明一次性投影到成员配置；[agents/assembly.py](../../src/tlon/agents/assembly.py) 的 `assemble_member_services` 按成员配置装配。即使 Docker 不可用，L1/L2 仍按声明注册工具，调用时返回装置未初始化错误。

内置原型按实际行为声明：定址转发、条件路由、短回合交流、标准无执行迭代为 L0；标准文件迭代为 L1；计划后执行、标准工具迭代、证据核验为 L2。原型技术 ID 均为系统生成的 p-UUID，显示名不代表职业（ADR 0035）。L2 声明在原型评审中作为红旗呈现，不自动构成拒绝。

L1 是文件层，没有 WASM/WASI 运行时。更强隔离的 gVisor/microVM 也不是当前实现。

## 接口与审计

[sandbox/device.py](../../src/tlon/sandbox/device.py) 定义 `ExecutionDevice` Protocol：

```python
async def run(program: str, timeout: int = 60, workdir: str | None = None) -> ExecutionResult
async def check_path(path: str, allowed_paths: list[str], *, write: bool = False) -> None
async def read_file(path: str) -> bytes
async def fetch_archive(path: str) -> bytes
async def write_file(path: str, content: bytes) -> bool
async def file_exists(path: str) -> bool
def close() -> None
```

`ExecutionResult` 只有 `exit_code`、`stdout`、`stderr`。`close()` 幂等释放客户端连接，不删除文件柜；关闭后的装置实例不再复用。

装配层通过 `AuditedExecutionDevice` 包装装置。每次正常返回的 `run()` 记录 `device.exec` span 与 `device_exec` 日志，包括成员、工作目录、命令前 200 字符、耗时、退出码和超时标记。完整工具调用存在 session journal 中；文件 I/O 和生命周期仅透传，不因此获得逐次 exec 审计。

接口保持当前实现需要的最小形态，不提供文件 diff、资源计量 outcome 或通用 inputs/budget 数据结构。

## 双容器模型与生命周期

[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py) 的 `DockerRuntime` 实现两种容器：

- **文件柜**：`tlon-agent-{agent_id}`，创建后不启动，只经 Docker archive API 服务文件 I/O。L1/L2 共用这一机制。
- **一次性执行单元**：`tlon-agent-{agent_id}-exec-{uuid8}`，仅 L2 每次 `run()` 创建，start → wait → 分别取 stdout/stderr → finally 强制删除。避免 auto_remove 与取日志竞态。

`run()` 在非 L2 时立即拒绝。L2 命令使用容器内 `timeout` 加外层 `asyncio.wait_for(timeout + 5)`；超时返回退出码 -1 和 `Command timed out after … seconds`。默认镜像使用 coreutils；busybox timeout 作为 PID 1 时可能依赖外层兜底，最长多等待约 5 秒。

取消发生于容器 create/start 的阻塞线程尚未结束时，宿主等待该步骤完成后清理已创建容器，避免晚完成任务泄漏。成员自己启动的后台进程不跨命令存活；`background=true` 使用宿主任务包裹一次性容器，由 `shell-check` 查询。`/tmp`、根文件系统变化和进程状态随执行单元消失。

文件柜跨睡眠和进程重启保留，但不是永不重建。`ensure_running` 比对镜像、挂载、内存/CPU 限额、网络、只读 rootfs 和 tmpfs，发现配置漂移便移除重建；workspace 在宿主挂载上，重建不删除它。显式删除成员与演武场拆除另有生命周期，不由 `close()` 隐式完成。

## 挂载与文件通道

两种容器共用 `_container_kwargs` 与 `_bind_mounts`：

| 宿主数据 | 容器位置 | 权限 |
|---|---|---|
| 成员 workspace | `docker.working_dir`，默认 `/workspace` | 读写 |
| 整个成员家目录 | `/tlon-home` | 只读 |
| 容器 rootfs | `/` | 只读 |
| 临时 scratch | `/tmp` tmpfs，64 MiB | 读写、随容器丢弃 |

成员可读取自己的行为定义、人格和记忆；邮箱通过邮件工具访问共享邮局，不靠成员目录里的旧邮件文件。只有 workspace 的写入跨执行单元持久保存。

文件领域服务经 path_guard 做容器路径白名单检查，再由装置 check_path 检查目标及其祖先，拒绝符号链接路径。读侧额外允许 `/tlon-home`；写/edit 使用配置的写白名单。归档解析拒绝绝对路径、`..` 等非法条目，链接仅列出而不读取其目标。Docker stat 与 archive API 并非同一个原子 open-beneath 操作；并发 L2 命令改写路径仍有 TOCTOU 窗口，不能声称已消除任意并发 symlink 竞态。成员挂载隔离与内核只读挂载仍是最终边界，其他成员目录和宿主 Docker socket 不进入容器。

`read_file` 与 `write_file` 使用 get_archive / put_archive，字节不经 stdout 解码；MemberFilesystemService 在领域边界另要求 UTF-8 文本。`file_exists` 读 archive 响应头；list/search 把目录 tar 拉回宿主分析，由 [sandbox/archive.py](../../src/tlon/sandbox/archive.py) 完成遍历与匹配，不依赖镜像内 find/grep。搜索内容正则采用 Python `re`。写入父目录通过 tar 目录条目创建，只加入缺失父目录，避免修改已有目录元数据。

## 网络与资源

网络默认 `none`；成员配置可显式声明其他 `docker.network_mode`。文件柜和执行单元使用相同网络配置。默认断网不是对所有成员配置都强制断网。

每容器内存/CPU 限额由 Docker 执行。[sandbox/resource_monitor.py](../../src/tlon/sandbox/resource_monitor.py) 的 `ContainerResourceMonitor` 在后台采样，按成员归并执行单元，向 metrics 提供内存、CPU 与社会汇总的内存快照；metrics 读取不阻塞等待 Docker。daemon 不可达时记录警告并返回空账本。

社会级 `TLON_CONTAINER_MEMORY_BUDGET` 目前只报警，不拒绝激活。每日 CPU 秒、磁盘总量和社会级强制预算尚不存在，不能把它们当作已有安全保证。

## 历史与未实施方向

此领域的建设经过接口化、只读 rootfs、默认断网、资源监控、一次性执行单元和 L1 文件层等步骤；历史设计见执行装置相关 Issues #21–#26、#31、#40，当前规则以上述机制为准。行为与执行授权的关系参见 [DSL 设计](dsl.md)。

尚未实施：workspace 自动 Git 提交与 diff 审计、通用资源计量结果、社会级强制资源闸、替代隔离运行时、egress 代理。它们是后续方向，不是执行装置已承诺的接口或能力。突变直接起草为 tlosure artifact，已由能力 staging 通道实现，不再以 workspace 程序为起点。
