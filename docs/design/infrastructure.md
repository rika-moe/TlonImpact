# Runtime 基础设施设计

本文负责组件装配、事件通知、路径与配置注入、进程级观测设施。成员调度和会话循环见 [runtime](runtime.md)，服务端客户端会话见 [server-protocol](server-protocol.md)。系统边界在 [architecture](../architecture.md)，拆包的历史理由见 [ADR 0019](../decisions/0019-runtime-server-client-split.md)。

## 领域约束

- 依赖显式传入，组件依赖图保持无环；业务资源的创建者负责生命周期清理。
- 事件通知与显式调用并存。facade、查询和投信请求可经注入对象调用；EventBus 承担发布/订阅通知，不承诺持久投递。
- `TlonPaths` 和 `Settings` 是显式实例依赖，业务状态不得由隐藏单例跨 runtime 共享。
- runtime 的创建与关闭不接管整个进程的 OTel provider；观测 provider 由进程编排层管理。

## 当前组件与装配

[Component](../../src/tlon/base/component.py) 提供基础生命周期：构造时存依赖并轻量初始化，`initialize()` 订阅和启动任务，`shutdown()` 退订、取消任务和释放资源。并非所有对象都必须继承 Component：facade、值对象和资源包装按其职责使用自己的接口。

Component 源文件还保留「仅 EventBus 通信」「ServerApp 唯一装配者」的旧概括。当前装配以 [api/bootstrap.py](../../src/tlon/api/bootstrap.py) 的 `build_runtime` 和 [serverapp.py](../../src/tlon_server/serverapp.py) 的 `ServerApp.initialize` 为准：两层分别装配，facade/端口直接委托是正常路径。本次只整理开发文档，未改动这些源文件注释。

当前启动次序：

1. 入口解析数据根、加载 `.env`，创建进程设置快照；ServerApp 初始化 tracing/metrics。
2. `build_runtime` 播种内置原型和技能，不创建标准成员团队。
3. 建立 EventBus、ConfigService、ContainerResourceMonitor、MetricsService 和 FileTraceStore。资源监控的快照查询在构造 MetricsService 时注入，不使用 setter。
4. 构造 guardian 信息提供者、外部网关注册表、地址解析器和 PostOffice，在线程中初始化邮件存储。
5. 构造 AgentRegistry，注册配置中的成员并初始化；再构造退休蒸馏、交接服务和 Adam。Adam 的 boot 策略可以发起成员启动，runtime 装配期间不打开 HTTP 端口不意味着成员从不运行。
6. 装配人类适配器和可选 GitHubAdapter、注册外部网关，构造各 domain facade，返回 Runtime。
7. ServerApp 装配 SessionManager、NotificationService、MemberDirectory、ClientGateway 和 RESTServer，发布 SYSTEM_STARTUP；`start()` 才开始监听。

关闭时 ServerApp 先结束 HTTP 与客户端连接，再调用 `Runtime.shutdown()`。runtime 先关 Adam 与可选外部适配器，再关注册表、退休服务、监控、人类适配器和 EventBus；进程观测设施在编排层收尾。顺序的依据是依赖和资源所有权，不以「严格等于构造顺序倒放」代替实际实现。关闭防重入，`run_server` 在 `finally` 中调用清理；装配中途抛错且尚未返回的局部资源不能仅凭这一结构宣称全部清理有保证。

ArenaFacade 启动的后台评估任务当前不纳入 Runtime.shutdown，进行中 run 的进程内记录不会跨进程恢复；磁盘证据另行保存。它是当前生命周期限制，见 [arena](arena.md)。

## EventBus 的通知契约

实现为 [EventBus](../../src/tlon/base/events/event_bus.py)，事件枚举在 [EventType](../../src/tlon/base/events/event.py)。每个订阅独享一个有界 `asyncio.Queue` 和消费 task。

| 情况 | 当前行为 |
|---|---|
| 发布 | 对匹配订阅逐个 `put_nowait`，不等待处理器完成 |
| 队列容量 | 每订阅 1000（`SUBSCRIBER_QUEUE_MAX_SIZE`） |
| 队列溢出 | 丢弃该订阅的新事件，保留已排队事件并记 warning |
| 顺序 | 同一订阅 FIFO 串行；订阅之间无顺序保证 |
| 处理器 | 异步处理器在事件循环 await；同步处理器在线程池运行并等待完成 |
| 处理器异常 | 记录错误，后续事件继续，其他订阅不受影响 |
| 退订 | 取消该消费任务，丢弃尚未消费的事件 |
| 停总线 | 取消全部消费任务，有界等待 5 秒；队列不落盘 |
| 未启动时发布 | 抛 RuntimeError |

因此 `await publish()` 仅表示入队尝试结束，不表示订阅者已经处理，更不表示邮件已持久化。邮件先由邮局提交，再通知订阅者；成员从持久未读恢复漏通知。其他事件是否需要补偿，应由其业务消费者定义，不能假设总线具备统一持久重放能力。

## 路径与设置

[TlonPaths](../../src/tlon/base/paths.py) 集中构造路径；访问器没有目录创建副作用，写入方在写入处创建父目录。进程默认根 `~/.tlon` 可以自动创建；显式 `TLON_HOME` 指向不存在目录或非目录时拒绝启动，不静默落回真实数据根。测试和隔离 runtime 可直接传入独立 home。

[Settings](../../src/tlon/base/settings.py) 是进程环境配置的不可变快照。`.env` 从该数据根加载，真实环境变量优先。模型选择属于成员配置，不是进程级 LLM_MODEL。字段、完整目录树和加载优先级见 [配置参考](../reference/config-reference.md)。

## 观测设施与隔离例外

| 设施 | 所有权与限制 |
|---|---|
| OTel TracerProvider | 进程级；`init_tracing` 的幂等守卫使首调参数生效，后续 runtime 不获得独立 provider |
| OTel MeterProvider | 进程级；由 `init_metrics` / `shutdown_metrics` 管理 |
| structlog.configure | 进程输出格式，由进程编排入口配置 |
| structlog contextvars | task-local 上下文，不能与进程共享业务状态混为一谈 |
| FileTraceStore | 显式路径实例，但 trace 写入经过进程级 provider，不能因此推断多 runtime 观测完全隔离 |

源码锚点：[otel.py](../../src/tlon/base/tracing/otel.py)、[metrics.py](../../src/tlon/base/metrics.py)、[logger.py](../../src/tlon/base/logger.py)、[trace_store.py](../../src/tlon/base/tracing/trace_store.py)。词汇表等模块常量不等于业务单例，但可变容器仍需避免在运行时写入。

邮件投递 trace 关联到唤醒请求与后续处理；本地追踪通过 FileTraceStore 保存并由观测 API 查询，可选 OTLP 外发。观测用于诊断，不承担邮局和 journal 的事务权威。

## 验证

包依赖由 import-linter 检查；业务多实例隔离由 [test_multi_runtime.py](../../src/tests/integration/test_multi_runtime.py) 覆盖。`scripts/check_all.sh` 是统一质量门入口。上述测试不证明每种中途初始化失败均能完整回收，也不证明进程级观测设施完全隔离。
