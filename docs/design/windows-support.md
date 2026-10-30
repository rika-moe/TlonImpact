# Windows 原生运行适配

用户批准按三个阶段推进，每阶段独立 PR；本设计不涉及安装包分发。

1. **跨平台文件锁**：评审、人工修改和发布使用同一目录锁身份，保持跨进程互斥。
2. **进程生命周期**：调整服务信号处理及测试子进程管理，验证启动、邮件和正常退出。
3. **workspace 文件边界**：保持现有接口与安全约束，验证 Windows 句柄、reparse point、硬链接和替换竞争，再实现 Windows 文件访问。

## 第一阶段：跨平台目录锁

- 支持范围是提供操作系统原生文件锁的本地管理目录；不保证不支持原生锁的网络文件系统。
- 将现有间接依赖 `filelock` 声明为直接依赖，使用其平台原生 `FileLock`。
- 目录锁统一放在规范化目录的父目录下 `.tlon-locks/<目录名>.lock`。当前原型目录均在 Tlon 管理目录中；锁不进入原型目录枚举，也不进入成员 workspace。
- 发布锁 `target.parent`，评审锁 `prototypes-staging`；相同目录必定映射到相同锁路径，包括符号链接别名。
- 锁文件生命周期完全交给库管理，不自行删除活跃锁文件。锁依赖操作系统，进程异常退出后可以重新取得；不使用应用层过期时间抢锁。
- 保持临时目录完整写入后整体 rename 的发布方式，以及已存在目标拒绝覆盖的行为。
- 当前调用不存在同一目录锁的递归获取；审批持有 staging 锁后获取 trusted catalog 锁，顺序保持不变，不增加重入或锁注册表。
- 目录锁仅用于 Tlon 管理目录，不是面向不可信路径的文件访问接口，也不能代替 workspace 安全隔离。

验证覆盖跨进程竞争、异常终止后的锁释放、评审与发布共享锁身份、重复发布及既有评审竞争测试。评审回执在所有平台 flush + 文件 fsync 后 replace；POSIX 额外 fsync 目录，Windows 不执行不支持的目录 fd 操作。这不声称 Windows replace 具备相同断电持久性保证。第一阶段不代表已支持 Windows：服务生命周期和 workspace 的平台差异仍需后续处理。

Windows CI 使用 `bash scripts/check_all.sh --windows-locks`，实际执行跨进程锁、原型评审、staging 和发布操作测试；测试选择只在质量门脚本维护。

## 第二阶段：服务生命周期

- Ctrl+C 由 `asyncio.run()` Runner 取消主任务；Unix SIGTERM、Windows SIGBREAK 请求一次取消，统一由 `run_server` 的 finally 关闭组件。重复停止信号不打断清理，退出恢复原信号处理器。Windows terminate 是强杀，不承诺执行清理。
- 集成测试直接使用当前 Python 运行 launcher 模块。POSIX 继续回收整个 session 进程组；Windows 使用 Job Object（KILL_ON_JOB_CLOSE），先通过 stdin gate 阻塞执行，加入 job 后才运行目标，避免产生未受管后代。Job handle 仅测试控制进程持有。
- Windows 正常停止发送 CTRL_BREAK_EVENT，超时终止整个 job；覆盖父先退出、后代仍持输出管道与端口，以及忽略停止信号的场景。该工具只属于测试基础设施。
- `scripts/check_all.sh --windows-smoke` 是 Windows 生命周期、真实 CLI 及邮件入箱验证的单一测试清单，CI 调用该分组。此阶段仍不宣称 Windows workspace 文件边界可用。

Windows 进程树实现依据 [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) 的子进程继承和最后句柄关闭语义；pywin32 的限制字典结构依据其 [win32job 源码](https://github.com/mhammond/pywin32/blob/main/win32/src/win32job.i)。launcher 进程入口将真实 stdout/stderr 文本流配置为 UTF-8，使重定向的中文和 emoji 输出不依赖 Windows 代码页；StringIO 等嵌入流不变。测试不注入 UTF-8 启动选项，并以 PYTHONIOENCODING=ascii 验证真实 CLI 能启动、输出完整 banner 并正常退出。
