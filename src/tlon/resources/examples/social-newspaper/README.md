# 社会文摘：普通 Lisp 报社

本目录是可复制的 L0 成员程序，不会在启动、安装或升级时创建成员，也没有自动订阅者。通过正常成员/原型创建流程安装 behavior.tlon、modules/ 和身份 Markdown 文件，配置成员级 llm.model_name 和对应 provider 凭证。无需设备沙箱。

启用前，将 modules/social-newspaper/main.tlon 中的 operator@tlon.internal 改成实际编辑邮箱，保留 postmaster@tlon.internal 以接受通用 cron 的触发。名单是报社自己的出版授权；普通订阅者不能出版，邮件正文无法改变名单。

| 主题 | 行为 |
| --- | --- |
| subscribe | 真实发件地址订阅，不接受代订 |
| unsubscribe | 真实发件地址退订，不改变已冻结刊期 |
| publish | 编辑或 postmaster 出版，刊期取该触发邮件 timestamp 的 UTC 日期 |
| publish:2026-10-25 | 编辑或 postmaster 指定期号，用于手动出版及重试分发 |

主题区分大小写，不去空格。未知主题只标读。无人订阅时不查询材料、不调用模型、不冻结期号。无指标、无扫描成员、失败记录及待审批提案时视为空材料，冻结空期，不调用模型。相同期号重试复用冻结内容及名单，不重新采集；需要新材料就使用新期号。

## 启用周期触发

在 ~/.tlon/cron.yaml 声明静态任务，将 to 改成实际创建的报社成员 ID（不是显示名或邮件地址），重启 dream 生效：

```yaml
- name: society-newspaper
  to: replace-with-actual-member-id
  subject: publish
  content: 执行本报的周期出版流程。
  cron: "0 9 * * *"
```

cron 表达式使用宿主本地时区；报社按触发邮件的 UTC 日期划期，两者独立。恢复不会改用恢复时钟。周期调度不补发停机期间错过的刊期；需要补刊时发送 publish:<期号>。使用 interval_seconds 替代 cron 时，首轮也等待该间隔。postmaster 是系统发件地址，不是订阅人或可投递收件人。

## 采编与恢复

采编函数显式读取 metrics-read、social-health 和 prototype-staging-list，仅把邮件收发总计、扫描/失败数、未回复候选数量、待批准提案总数和前 10 条概要送给模型，提案描述最多 200 字。没有全社会邮箱、全文轨迹或人类收件人枚举，也不声称穷尽社会活动。提案工具当前仍读取整个暂存区，Lisp 只限制进入模型的概要数量。social-health 当前实现仍需要扫描邮箱元数据，返回候选至多 20 项；本例只采用计数，不把扫描成本误称为固定常数。进程累计指标会重启清零，不能视为当日增量；入箱不等于已读，未回复不等于未送达或失职。

Lisp 直接选择一次 model-call 编辑正文；Python 工具没有额外隐藏的编辑模型。tlon.newspaper 普通模块共用订阅、刊期校验、冻结和分发。模型截断、空白正文或返回工具调用时不冻结、不寄报，可重新发出版命令。

刊期持久状态位于成员 program.sqlite3 的 edition:<期号>，包含正文、观测来源、冻结订阅名单及逐地址回执。附录 observation:<期号>:<工具名> 是工具与刊期的合成标签，不是实际 run ID；附录另附真实出版触发邮件 ID，对应 program run 中持久的实际观测调用，不冒充投稿邮件 ID；原始工具结果可从执行记录核查。正文不是事实核验。

分发 request_id 为 edition:<期号>:<地址>，相同期号恢复复用。仅邮局 accepted 且带 mail_id 的回执记为成功，拒收或结果未知不标成功。已成功地址不重发；未知结果不会通过换键自动重发。网关仍是既有尽力投递语义，本地入箱不等于外部送达。
