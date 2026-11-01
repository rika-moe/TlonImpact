# Gitea 软件协作设计

> 决策见 [ADR 0055](../decisions/0055-managed-gitea-collaboration.md)。#256 已实现持久服务、初始管理身份/组织与启动状态；#257 增加成员身份和执行接入；#258 增加个人 fork + 上游 PR 的项目治理；#259 已实现通知桥接。下节说明当前机制与实测边界；Linux/Windows 回调未实测，不作为已通过能力声明。

## 当前实现：托管服务（#256）

`build_runtime()` 只装配 [GiteaFacade](../../src/tlon/api/gitea.py)，不创建容器或打开端口。`ServerApp.start()` 在核心 HTTP 就绪后后台执行初始化，因此拉取镜像、Docker 不可用或端口冲突不会阻断邮件。启动终端输出地址与结果，`GET /admin/gitea` 经原有 WebUI 请求边界返回 `not_started` / `starting` / `ready` / `unavailable`；公共 `/health` 不返回 Gitea 身份或路径。

实现分为 [service](../../src/tlon/gitea/service.py)（异步生命周期）、[storage](../../src/tlon/gitea/storage.py)（私有记录）、[docker_service](../../src/tlon/gitea/docker_service.py)（容器与挂载）、[initialize](../../src/tlon/gitea/initialize.py)（HTTP/账号/组织）及 [control](../../src/tlon/gitea/control.py)（截止与取消）。包依赖由 import-linter 约束，serving 只通过 facade 使用服务。

- 固定镜像 `docker.gitea.com/gitea:28.2.0-rootless`；SQLite；`TLON_GITEA_PORT` 默认 3000，范围 1–65535，发布到 `127.0.0.1`。不自动升级，不支持远程 Gitea 模式。
- 数据位于 `{TLON_HOME}/infrastructure/gitea/`：`state.json` 保存实例 UUID、规范路径和端口；`data/` 与 `config/` 是 Gitea 持久挂载。端口或路径变更、状态损坏以及容器归属/配置冲突均拒绝，不静默接管或重建。
- 每次启动使用实例文件锁及挂载读写往返探测。Docker daemon 必须真正共享数据目录；Colima 默认仅共享用户家目录，未共享的 `/private/tmp` 不能作为 Gitea 数据根。探测使用同镜像的无网络短命容器，结束删除，仅持久服务被保留。
- 服务按宿主非 root UID/GID 运行；root 宿主和 Windows 使用 1000:1000。设置 `USER=git` 供官方 Gitea 的用户名解析使用；不使用 root 运行 Gitea、不放宽目录到 777。平台实际文件共享权限仍须满足挂载探测。
- 在首次启动前通过镜像环境配置锁定安装入口、关闭公开注册、要求登录、禁用 SSH 和 Actions。使用不带认证的 `/api/healthz` 确认数据库/缓存就绪，再初始化账号，避免未创建管理员时以认证 API 判定健康造成死锁。
- 宿主管理账号 `tlon-service` 与人类账号 `tlon-operator` 分离。人类账号为私有组织 `tlon` 的 owner，首次登录强制改密；创建基础只读 `members` 团队，成员对账将有效成员加入该团队。重启查询已有账号、组织和团队，不重置密码；组织返回的 visibility 必须为 private（创建响应也校验），缺失、非私有或团队配置漂移均明确报错，不自动修改组织可见性。
- `credentials.json` 保存宿主初始化密钥，`operator-credentials.json` 仅交付人类账号的初始密码。目录/文件采用私有权限；状态与日志只暴露人类凭据文件路径，不返回密码。首次网页登录自行读取该文件，改密后文件中的初始密码不再有效。
- 启动工作使用 300 秒协作预算，HTTP/Docker 请求设超时，镜像流按进度检查截止；管理员 CLI 使用容器内 30 秒 kill 截止及宿主 detached exec 轮询。退出发取消信号并等待正在进行的有限 IO 与清理，不留下仍会继续创建容器的后台线程；总预算不是能中断内核或 Docker daemon 的硬实时保证。
- 退出只释放集成任务和客户端，持久容器使用 `unless-stopped` 重启策略。服务停止、升级、删除和数据恢复仍由明确的管理操作完成，当前没有新增自动销毁或迁移命令。

`ready` 表示本轮服务初始化及配置检查通过，是启动结果快照，不是持续存活监控。运行中手工停止 Gitea 后，重启 dream 会再次确保可用；当前状态端点不自动探活或重启服务。成员同步状态单独报告；项目治理按请求查询实际状态，通知桥接独立报告就绪与投递状态。

## 当前实现：成员与执行接入（#257）

宿主按稳定 member ID 的 SHA-256 派生 Gitea 用户名，在私有 `members/` 记录绑定、密码及执行 token。账号不随显示名或运行容器重建而变化。对账在服务初始化后后台运行；新增成员不等待 Gitea，失败通过 `GET /admin/gitea` 的 `members` 状态暴露并自动重试。休眠成员仍是有效成员；退休以邮件库的持久退休事实为准，删除成员也撤权。退休先禁止登录，再撤销全部 token、退出组织，保留账号与历史；撤权失败保留待处理状态，不能因为进程重启丢失。继任 ID 创建新账号，不能复活旧绑定。

成员执行接入使用现有配置的明确授权值：

```yaml
docker:
  level: L2
  network_mode: gitea
```

`gitea` 是实例网络的逻辑名称，不是一个全局 Docker 网络。默认仍为 `none`；该值不提升 L0/L1 的执行等级。授权成员的一次性执行单元使用随包提供配方构建的 Git/tea 镜像；文件柜仍使用声明的基础镜像且不联网。该模式下执行镜像由集成管理，`docker.image` 仅决定文件柜镜像。镜像固定 Git `1:2.34.1-1ubuntu1.18` 与 tea `0.16.0`（amd64/arm64 下载均校验 SHA256），按配方内容派生 tag 缓存，首次执行需要构建；构建失败返回执行失败。构建使用流式取消与请求超时，退出等待宿主 worker 结束；关闭客户端流不等同于保证 Docker daemon 的构建任务立即停止。

每次执行通过注入端口获取本成员凭据，并检查成员仍存在、未退休；sandbox 不读取宿主凭据文件。临时容器环境提供 `GITEA_INSTANCE_URL`、`GITEA_TOKEN`（tea 原生支持）和 `TLON_GITEA_URL`、`TLON_GITEA_USERNAME`、`TLON_GITEA_TOKEN`。Git 使用预装 credential helper，精确匹配内部服务的协议与主机，禁用 HTTP 重定向，不依赖可写 HOME 或上次容器的登录文件。tea 启动仍需创建配置目录，`XDG_CONFIG_HOME=/tmp/tlon-config` 指向每次执行的 tmpfs；不执行 `tea login`，认证仍来自环境。工作副本的 remote 保留无凭据 URL。环境凭据对本成员的代码可见，不能防止成员主动输出自己的 token；宿主管理密码不进入执行单元。

浏览器继续使用 `http://127.0.0.1:<port>/`；成员 clone 使用 `$TLON_GITEA_URL/tlon/<repo>.git`，tea 通过 `--repo tlon/<repo>` 指定仓库。不能直接在容器使用 API 返回的浏览器 clone URL。每个临时执行单元保留既有只读 rootfs、workspace 挂载及资源限制，完成后清理；不挂 Docker socket。该网络是 Docker bridge，授权也意味着接入该网络的网络能力，并非只允许 Git 请求的应用防火墙。

本阶段仅建立默认阅读/讨论身份。#257 的 push 验收仅使用临时仓库受控授权；个人 fork + 上游 reviewer 授权由下一节的 #258 提供。

## 当前实现：个人 fork 与上游治理（#258）

WebUI 设置页提供私有组织项目的创建、已有仓库纳管、保护规则替换及 reviewer 合并授权；fork、提交 PR 和评审使用 Gitea 原生界面或 Git/tea，不新增 Tlon 开发工作流按钮。管理接口继续使用现有浏览器来源边界，只接受稳定 member ID，宿主解析为绑定账号；成员执行 token 不用于调用这些管理接口。

[ProjectManager](../../src/tlon/gitea/projects.py) 直接查询 Gitea 的仓库、团队、成员和保护规则，不保存另一份本地授权表。每个项目以仓库 ID 命名一个 reviewer 团队，只绑定该仓库，授予 code/PR write；默认 `members` 团队仍为 read。普通成员没有上游写权，在自己的私有 fork 中开发，不需要额外开发授权。

新项目自动初始化默认分支，默认名称为 `main`；纳管已有项目保留其实际默认分支。默认分支总受保护，设置页预填额外规则 `develop, release/**`，人类可显式替换。保存先建立所需保护，再移除不再需要的已有托管规则；未知权限、跨项目团队、直接协作者或冲突的外部规则均报告问题，不静默接管。项目状态和授权变更都读取实际配置，校验失败不会显示成功；多次 Gitea API 请求不构成事务，部分失败应检查实际状态后重试。

受保护分支禁止直接推送、强推和删除，合并名单仅包含项目 reviewer 团队及人类账号 `tlon-operator`，不允许管理员绕过正常合并检查。reviewer 获得上游写权但不是仓库管理员；其未保护分支仍遵循 Gitea 原生 write 权限。个人 fork 不注入上游保护规则，可正常强推和删除自己的开发分支。

固定 Gitea 28.2.0 实测确认：成员的 `max_repo_creation=0` 禁止新建普通仓库，但镜像默认 `ALLOW_FORK_WITHOUT_MAXIMUM_LIMIT=true` 允许原生 fork，不需为此提升账号权限。退休会禁用账号、撤销 token 并退出组织，保留 fork 与已打开 PR；已打开 PR 的提交仍可经上游 PR ref 获取和合并。私有 fork 不保证 reviewer 能直接 clone，评审使用上游 ref，具体命令见下文。

## 当前实现：通知桥接（#259）

独立 webhook listener 由 dream 托管，`TLON_GITEA_WEBHOOK_PORT` 默认 `3001`，监听 `0.0.0.0`，仅提供实例专属的 `/gitea/{instance_id}` 回调路径，不挂载管理 API。已有 WebUI/管理入口仍保留原来的 loopback、Host 和同源请求边界。回调必须通过原始请求体的 HMAC-SHA256 验签、1 MiB 请求大小限制及实例核对；签名通过后还会查询 Gitea 当前项目治理，精确匹配受管私有仓库 ID 与 `tlon/<repo>` 全名。

宿主探测 Gitea 容器可达的回调地址，在受管 `app.ini` 中设置精确的回调 IP 与端口 allowlist，不放行任意私网或整个局域网；必要时重启持久 Gitea 容器使配置生效，保留数据和容器身份。写入配置前持久保存待重启标记，重启失败或崩溃后继续恢复，健康检查通过才清除标记。启动从实际 Gitea 容器发起带签名的 ping，连通性验证成功后才进入桥接就绪流程。macOS + Colima 的回调通路已有实测；Linux 与 Windows 尚未实测，各实例仍须实际 ping 成功，不能仅凭平台分支存在就宣称支持。

桥接为每个当前受管仓库创建实例 URL 对应的 Gitea webhook，关注 issue、指派、评论、PR、review 请求、review 和 PR 同步事件。按精确实例 UUID 路径识别自有 hook；回调 IP/端口变化时更新原 hook URL，保留密钥与 delivery 历史。重复实例 hook 或冲突类型会报错，不接管其他实例路径的 hook。API 配置使用 `pull_request_review` 事件组，接收使用 `X-Gitea-Event-Type` 区分三个具体 review 事件。作者、被指派者、请求的 reviewer、当前正文中的 mention，以及分页查询到的讨论/评审参与者组成候选收件人；用户名大小写不敏感，排除操作者，只映射到仍有效且可投递的成员。历史描述里的 mention 不会因每条新评论重复唤醒。

`webhooks.sqlite3` 在同一事务内保存事件、delivery ID、不可变邮件计划和待投递记录。邮件身份、时间、内容和稳定 mail ID 首次接受后固定；新 delivery ID 的重投不会重算已接收事件的投递计划。重启后即使 Gitea/回调暂不可用，已接收但投递失败的记录仍通过邮局自动重试，重试按尝试次数排列，避免前面的失败收件人阻塞后续通知；退休或不可投递成员记为跳过。邮件使用外部集成身份 `notifications@gitea.invalid`，接入原有邮局和调度，不强制唤醒休眠成员。

在 `GET /admin/gitea` 的 `webhooks` 字段查看状态、pending/failed/unrouted 计数及近期 delivery 与事件关联；WebUI 设置页刷新 Gitea 状态可看到同一结果。此状态与 Gitea 主服务启动快照分开：容器仍运行不代表回调 listener 在线，队列积压也不表示事件从未收到。

停机期间从未接收的事件不会出现在本地 pending 中。恢复 dream 后，应由有项目设置权限的人类在 Gitea 项目的 **Settings → Webhooks** 中打开本实例 hook，检查失败 delivery，并手工重投。固定版本已实测原生登录会话与仓库 hook 页面，再调用该页面的 `POST /tlon/<repo>/settings/hooks/<id>/replay/<delivery UUID>` 重投操作；它会复制原 payload 并生成新的 delivery UUID。同源保护会拒绝跨站重投；连续两次重投仅产生一封持久邮件。不要通过改写 payload 或新建评论模拟重投。

签名密钥保存在宿主私有 `webhook.json`，固定于 hook 创建时。Gitea 28.2 的 PATCH API 不更新 secret，GET 也不返回 secret；桥接只对账 URL、事件、启用状态和内容类型，不宣称自动修复密钥漂移。人工修改 Gitea hook secret 后，验签失败会在桥接诊断中呈现。确认密钥不一致时，先检查并保留需要的失败 delivery 记录，经人类确认后手工删除该实例 hook，桥接会用持久密钥重建；删除 hook 会丢失其 delivery 历史，因此程序绝不自动删除。

macOS + Colima、Gitea 28.2.0 与 tea 0.16.0 已实测 fork 开发、请求评审通知、评审意见通知、修订推送通知、reviewer 合并及人类 operator 合并；测试证据绑定最终 commit。隔离 dream 的 HTTP 投信与持久收件箱，以及退出后 Gitea 留存均通过。质量门结果见对应 PR。

## 目标与事实边界

每个成员以独立身份参与软件开发，人类和成员共同使用 Gitea 的仓库、issue、PR、review。Tlon 提供成员运行环境、身份接入和邮件唤醒；Git/tea 承担正常开发操作，不重写软件协作平台。

| 事实 | 权威来源 |
|---|---|
| member ID、执行等级、休眠与退休状态 | Tlon 成员系统 |
| 代码、分支、issue、PR、review、仓库权限 | Gitea |
| member 与 Gitea 用户的绑定、宿主凭据、桥接处理记录 | Tlon 实例私有集成存储 |
| 成员收到的通知邮件、已读与待处理状态 | Tlon 邮局 |
| 工作副本和本地测试产物 | 各成员自己的 workspace |

Gitea 项目权限不复制为另一套可独立修改的 Tlon 权限表。绑定记录不赋权，评审通过不自动赋予合并权，邮件不是 PR 状态的第二权威。

## 已核实的现状与接入点

- [执行装置](execution-device.md) 使用 L0/L1/L2；L2 为文件柜加一次性执行单元，rootfs 只读，workspace 持久化，网络默认 none。
- [DockerRuntime](../../src/tlon/sandbox/docker_runtime.py) 按现有镜像创建容器，不在启动时安装软件；每次执行创建并清理独立容器。因此 Git/tea 和认证适配需预装进镜像，凭据需每次注入，不能依赖上一次命令的进程环境或临时登录文件。
- 项目已依赖 Docker SDK、aiohttp 和 filelock，先复用已有依赖；不要求用户另装 Compose，不引入通用基础设施编排框架。
- [基础设施](infrastructure.md) 显式注入路径和设置；[邮件](mail.md) 先提交 SQLite 再通知；EventBus 不保证持久投递。
- [服务端协议](server-protocol.md) 的本地 WebUI 来源边界继续有效。Gitea 回调必须使用独立的机器认证入口。

## 服务生命周期与初始化

一套实例托管一个 Gitea 容器和一个组织。实例生成持久标识，容器和网络记录实例归属，不能仅靠固定全局容器名识别。并发 dream 的协调复用实例锁机制；不同 `TLON_HOME` 不共享账号、凭据、目录或容器。

dream 的启动编排执行以下步骤，任何一步失败都保留可诊断状态：

1. 检查 Docker、实例标识、持久目录、端口及现有容器归属。
2. 不存在时创建固定版本容器；已停止时启动；已运行时复用。版本或持久挂载不匹配时报告，不删除重建，不自动拉取 latest 升级。
3. 有界等待 HTTP 就绪，区分进程存活、安装完成和集成可用。
4. 首次自动初始化 SQLite、服务配置和宿主管理账号，关闭公开注册和安装入口；配置组织、人类管理身份和基础成员团队。
5. 按稳定 ID 对账成员账号；配置受管项目的权限、分支保护及 webhook。单个账号配置失败不回滚成员创建，不标记其 Git 接入成功，可再次对账。
6. 接通通知接收，展示服务地址及集成状态。浏览器登录使用独立人类账号，不向浏览器暴露宿主管理 token，也不自动登录或输出长期凭据。

初始化分步检查实际状态，重启不得重复创建对象。未知结果先查询 Gitea，再决定重试；不因本地标记缺失而重建已有组织或旋转所有凭据。

dream 退出只关闭客户端、接收器和后台桥接任务，不停止或删除 Gitea。停止服务、备份、恢复、升级及销毁数据必须是显式管理行为；本设计不授权自动执行这些操作。

Docker 不可用、端口冲突、服务超时均显示集成不可用，无关的 Tlon 邮件与 L0 能力仍可运行。恢复不能仅改状态为 healthy，需完成实际就绪和账号对账。

## 持久数据与版本

默认 SQLite；Gitea 数据、配置及 Tlon 集成状态归属于 `TLON_HOME` 的独立基础设施目录，由 `TlonPaths` 显式提供。具体子目录、字段和命令名称在实施时定型，不提前伪装成已支持配置。

宿主凭据存储不位于任何成员家目录或 workspace，不挂进成员容器。Gitea 数据也不得挂进成员容器。rootless 官方镜像作为首选，实施需验证宿主平台文件权限和持久卷行为；不能把 rootless 镜像理解为整个 Docker daemon 已无特权。

固定经过验证的 Gitea 和 tea 版本。升级独立于 dream 启动；备份包含 SQLite 一致性快照、仓库、附件、配置、绑定及必要密钥，使用受支持的停写或备份流程，不能以运行中直接复制数据库文件宣称备份一致。容器删除不删除数据，移动数据根后的重绑定必须显式核对原实例归属。

## 成员身份与凭据

| 生命周期 | 目标行为 |
|---|---|
| 新建成员 | 创建绑定稳定 member ID 的独立账号，加入默认阅读讨论团队 |
| 已有成员首次接入 | 按成员登记表配置账号，不要求重建成员 |
| 容器重建、dream 重启 | 复用账号，按绑定重新注入有效凭据 |
| 休眠、等待、临时停止 | 保留账号及权限 |
| 正式退休 | 禁用访问并撤销 token、团队写入和合并资格，保留账号与历史；失败显式记录并重试 |
| 创建继任者 | 新账号、新凭据；不继承上一代授权 |

所有成员可以拥有身份，但 L0/L1 不因账号创建而获得 Shell；第一阶段的 Git/tea 使用面面向具备执行与网络授权的 L2 成员。改变执行等级仍走现有授权流程。

宿主每次向执行单元提供该成员的服务地址、用户名和 token；不从模型请求中接受替代 member ID。成员可使用自己的凭据，不能读取宿主管理员或其他成员凭据。环境变量不构成对本成员执行代码的保密边界，不承诺它无法读取或主动输出自己的 token。

tea 采用所固定版本支持的非交互认证；Git HTTP 认证通过成熟 credential helper 或 askpass 读取环境。不得把 token 写进 remote URL、命令文本、workspace 或提示词。核实版本的实际环境变量与 helper 行为后再写配置参考，不能假设 CLI 登录会自动覆盖 Git 认证。Git 提交姓名/邮箱与 member 绑定用于归因，但 author 可由 Git 客户端填写；认证推送者、PR 作者和 review 身份才由 Gitea 认证保证，不把提交 author 当成身份验证。

## 项目和授权：个人 fork + 上游 PR

2026-11-01 用户修订：删除尚未合并的同仓库 developer/maintainer 双团队实现，采用 Gitea 原生 fork + PR。Tlon 管理身份与上游 reviewer 授权；fork、分支和 PR 由 Git/tea/Gitea 管理，不新增本地 fork 或权限数据库。

项目仓库归私有组织，由人类管理入口创建或纳管。所有有效成员能阅读上游、讨论、参与评审和 fork。开发者把变更推到自己账号下的 fork，再向组织上游发起 PR。拥有读取权限就能贡献代码，不再单独审批“开发权”。

| 身份 | 上游阅读、讨论、评审 | 自己 fork 写入 | 上游合并 | 改上游权限/保护 |
|---|---|---|---|---|
| 普通成员 / developer | 是 | 是 | 否 | 否 |
| reviewer（人类明确授予合并权） | 是 | 是 | 是 | 否 |
| 人类管理者 | 是 | 是 | 是 | 是 |

上游只有一个项目 reviewer 团队获得 code/PR write，默认 members 团队保持 read。普通成员始终没有上游写权限，因此不能通过向其他上游分支提交 PR 绕过合并授权，也无需用全分支保护分离同仓库的开发与合并权。reviewer 不是仓库 admin；只有人类管理入口能授予或撤销其资格，Gitea 仍是实际权限的权威。

上游默认分支及人类指定的集成/发布分支采用保护规则，禁止直接 push、force-push 和删除，合并允许名单为 reviewer 团队与人类管理者。保护适用于日常人类操作；人类仍保留显式调整治理配置的行政能力。不为个人 fork 注入这些限制，开发者可在 fork 中正常整理、强推和删除自己的开发分支。新集成分支需在管理流程中建立并配置保护。

固定 tea 0.16.0 的开发流程如下。示例项目为 `example`，成员的工作目录位于持久 workspace，所有 remote 均不含凭据；提交前按项目要求完成实现与测试。

```bash
tea repos fork --repo tlon/example
git clone "$TLON_GITEA_URL/$TLON_GITEA_USERNAME/example.git"
cd example
git remote add upstream "$TLON_GITEA_URL/tlon/example.git"
git switch -c feature/example
# 修改代码、运行项目测试，然后 git add 与 git commit。
git push -u origin feature/example
tea pulls create --repo tlon/example --head "$TLON_GITEA_USERNAME:feature/example" --base main --title "变更标题" --description "实现与测试证据"
```

上游和 fork 的浏览器地址与容器内部 Git/API origin 不同，容器命令使用 `$TLON_GITEA_URL`。reviewer 从上游读取 PR 信息，并从上游 PR ref 获取提交；不直接 clone 作者的私有 fork。以下以 PR 12 为例：

```bash
git clone "$TLON_GITEA_URL/tlon/example.git" example-review
cd example-review
git fetch origin refs/pull/12/head
git switch --detach FETCH_HEAD
# 评审并验证此 commit，随后在 Gitea PR 页面记录结论和合并。
```

退休禁用作者账号并撤销 token，保留 fork、提交和已有 PR 的历史；固定版本实测确认上述上游 ref 仍能 fetch，reviewer 仍可合并已打开 PR。继任成员用新账号/fork，不继承旧 reviewer 权限。

第一阶段由 reviewer 判断最终 commit 的测试证据和评审意见。参与评审不等于拥有合并权，不自动提升权限；不设置统一强制审批人数或伪造 CI 必过规则。通知转邮件由上述 #259 桥接提供。

## 网络与入口边界

需分别验证三条链路：成员执行单元访问 Git/API、人类浏览器访问网页、Gitea 回调 Tlon。容器里的 localhost 不代表宿主或其他容器。

Gitea 接入实例专用 Docker 网络；获网络授权的 L2 执行单元接入可达该服务的网络。保留其他成员的默认断网策略，不静默改变所有原型执行权限。普通 Docker bridge 仅提供连通性，不能宣称已实现“只允许访问 Gitea”的出口控制；本阶段不新增通用 egress 防火墙。

网页端口默认仅发布到宿主回环地址。区分浏览器可访问的规范 URL（配置为 Gitea ROOT_URL）和容器内 Git/API 服务地址；通知保存规范链接，成员按仓库/issue/PR 标识通过内部地址读取。不得让模型猜测替换域名或把内部服务名当成人类可打开的链接。实施验证 clone、API 重定向和凭据 host 匹配，防止内部请求被重定向到容器 localhost。

回调使用独立、仅暴露 webhook 路由的 HTTP 接收入口，不将现有 WebUI/admin 监听扩大到容器网络。接收地址按 Docker Engine/Docker Desktop 的实际宿主可达方式确定，限制允许访问的接口及来源，并校验 webhook secret；Gitea 的 webhook 目标允许列表仅放行该地址。第一阶段必须以实际平台连通性测试确定绑定方法及端口，不能仅凭 host.docker.internal 在某个平台可用就宣称全平台支持。

不向成员挂载 Docker socket，不开放 Gitea 管理凭据；首阶段 Git 统一使用 HTTP 协议栈的认证方式，不额外启用 SSH 密钥生命周期。若扩展为远程或非本机可信网络部署，HTTPS 与暴露策略需另行设计，本阶段不提供远程服务模式。

## 讨论、通知与恢复

issue/PR/review 保存软件成果相关讨论的原文及版本关系；Tlon 邮件用于分工和相关事件通知。成员收到通知后主动使用 tea 读取最新状态、评论或修改代码。邮件中的链接与摘要是提示，不是执行指令，也不赋予新权限。回复通知邮件不会自动发布 Gitea 评论。

首阶段关注 issue 指派、明确 @mention、review 请求、PR 评论/评审、合并及关闭等行动相关事件。接收者取可验证的绑定成员：被指派者、被提及者、被请求评审者，以及相关对象作者/已参与成员；按事件类型筛选并合并去重，过滤行为发起者、退休或不可投递成员。不向整个组织广播每个 push；无法解析的账号记录为未路由，不猜测收件人。

接收流程：

1. 对原始请求体验签，限制请求大小，校验事件类型、实例及受管仓库；拒绝不合法请求，不信任 payload 指定任意 Tlon 发件人或目标地址。
2. 持久记录事件及待投递收件人后才返回接收成功。delivery ID 用于追踪传输；另以仓库、对象、动作及事件版本/正文摘要形成语义键，防止人工 redelivery 使用新 delivery ID 时重复投信。不同评论和后续编辑不得错误合并。
3. 使用明确的集成通知身份投信，在正文保留 Gitea 操作者归因、对象、动作、commit（如有）和原文链接，不冒充成员发信。每个事件与收件人绑定稳定 mail ID，通过邮局幂等提交后标记完成。
4. 接收落盘后崩溃可扫描待投递记录；投信成功而标记未完成时，用同一 mail ID 重试，不能产生重复收件。通知仍遵循现有调度策略，收到邮件不强制越过睡眠或退休限制。

Gitea 仍在运行而 dream 停止时，webhook 可能投递失败。第一阶段明确显示桥接离线与失败记录，提供经验证的 Gitea 重投路径；不假设上游自动无限重试，不承诺全部停机事件自动补齐。恢复后的人工重投与已接收事件恢复是不同机制。未收到过的事件不能仅靠本地 pending 表恢复；自动离线对账属于后续增强。

### 事件版本的实现边界

普通评论使用原生 comment ID、动作、更新时间和正文摘要区分；不同评论和可辨识的后续编辑不会因正文相同而合并。Gitea 28.2 的 review webhook 只提供 `review.type` 和 `review.content`，没有原生 review ID 或独立评审时间；因此语义键结合 PR 更新时间、操作者、commit、评审类型与正文摘要。若两次评审产生完全相同的这些快照，两者无法从该版本 payload 区分，会合并为一次通知。这是首阶段的明确通知粒度限制，评审原文及各次记录仍以 Gitea 为准，不据此删除或合并 Gitea review。

## 包边界与职责

- `tlon_launcher` 负责进程入口；`tlon_server` 负责启动编排、独立 webhook HTTP 入口及本地管理展示，经 `tlon.api` facade 调用领域能力。
- `tlon` 负责 Gitea 生命周期、身份对账、凭据、事件持久处理和邮局接入；通过注入端口连接成员注册表、执行装置和邮件服务，保持现有内部依赖规则。具体模块布局实施时据 import-linter 定型，不让 sandbox 反向依赖成员或邮局。
- `tlosure` 不感知 Gitea。成员日常操作复用 shell-run 与 Git/tea；不增加第二套模型运行器，不批量生成 Gitea 专用 Operation。
- 宿主集成服务的 shutdown 释放客户端和后台任务；持久 Gitea 容器不属于随 runtime 关闭回收的临时执行资源，不复用成员一次性容器清理逻辑。

## 第一阶段闭环与后续范围

首阶段验收场景：人类创建项目和 issue，A、B 默认可读上游；A fork、clone、修改、在自己容器测试、push 到个人 fork 并向上游开 PR；B 经通知阅读 diff 并提出修改；A 修订；人类维护者检查最终 commit 的证据后合并。另验证明确授权的 member 能合并，未授权开发者不能合并。

测试报告至少包含 commit、命令、退出状态与结果摘要。测试来自开发成员的执行环境，不能作为独立 CI 通过信号。首阶段不启动 Actions runner，不设置不存在的必需检查，也不发布伪造的 CI 成功状态。

实施按依赖拆为可独立验证的切片：持久服务与状态展示 → 身份/镜像/认证/网络 → 项目权限和保护 → 通知桥接与完整协作验收。每片走独立提交或 PR，Git 操作及代码实施仍需用户明确授权；此文档不自行启动施工。

后续独立阶段：Gitea Actions runner、可信 CI 与合并质量门、自动补齐离线通知。远程 Gitea、多实例共享、SSO、自动权限晋升及部署发布不在第一阶段范围。

## 实施验收要求

| 范围 | 必须验证 |
|---|---|
| 生命周期 | 首启、重复启动、停容器后恢复、dream 退出仍可访问 Gitea、实例间隔离、归属冲突不误接管 |
| 持久化 | 重启及受控容器重建后仓库/issue/PR/账号仍在，备份恢复证据完整 |
| 身份 | 两成员身份不同、重建不换号、显示名变化不改绑定、退休撤权、继任不继承权限 |
| 执行 | 每次新执行单元都能认证，workspace 保留仓库，rootfs 隔离及无 Docker socket 不变 |
| 权限 | 普通成员可 fork/讨论但不能 push 上游；开发者可写个人 fork 但不能合并上游；reviewer 可合并但不能提权；直接推上游主分支被拒绝 |
| 网络 | 浏览器、容器 Git/API、回调三向实测；admin API 不能因接入被开放；L0/L1 不获得 Shell |
| 通知 | 正常验签、错误签名拒绝、收件人路由、重复投递与重投去重、接收/投信提交边界崩溃恢复 |
| 不可用 | Gitea/Docker/回调故障明确呈现，核心邮件仍可用；停机事件重投限制可复现 |
| 端到端 | 两成员完成开发—评审—修订—合并，测试证据绑定最终 commit，不冒充 CI |

代码实施需运行 `scripts/check_all.sh` 全量质量门，以及隔离 `TLON_HOME` 的 dream + HTTP 投信 + 持久收件箱检查；Gitea 场景使用独立临时数据、端口和容器，结束后显式清理测试资源，不能让“退出保留 Gitea”导致测试资源泄漏，也不触碰真实 `~/.tlon`。各阶段的验证记录随对应 PR 提供；上述当前能力不代表尚未实施的通知闭环已通过验收。

## 外部依据

以下是产品能力依据；部署时以固定版本实测为准，不以滚动文档代替验收。

- [Gitea 官方 rootless Docker 部署](https://docs.gitea.com/installation/install-with-docker-rootless/)：镜像、网络、持久卷与启动配置。
- [Gitea 配置说明](https://docs.gitea.com/administration/config-cheat-sheet/)：ROOT_URL、数据库、注册与 webhook 配置。
- [Gitea 权限模型](https://docs.gitea.com/1.22/usage/permissions/)与[受保护分支](https://docs.gitea.com/1.27/usage/access-control/protected-branches/)：团队 unit 权限和合并允许列表；引用版本不同，实施须在同一选定版本验证组合效果。
- [Gitea API 用法](https://docs.gitea.com/1.26/development/api-usage)：认证与 token scope。
- [Gitea webhook](https://docs.gitea.com/usage/repository/webhooks/)：事件、签名、delivery 标识和重投。
- [Gitea tea](https://about.gitea.com/products/tea/)：官方命令行客户端。
