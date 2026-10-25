# 来信报：普通成员的 Lisp 工作流

本目录是可复制的 L0 成员示例，不会在首装或升级时自动创建。通过正常成员/原型创建流程安装 behavior.tlon、modules/ 和三个身份 Markdown 文件，配置成员级 llm.model_name（例如 deepseek/deepseek-v4-flash）及 provider 凭证。无需设备沙箱。

办报流程位于 modules/newspaper/main.tlon，共用 tlon.newspaper 普通模块的订阅、刊期校验、冻结与分发；Python 只提供通用状态、模型与邮件效果。

启用前把 modules/newspaper/main.tlon 中的 operator@tlon.internal 改成实际编辑邮箱；保留 postmaster@tlon.internal 允许周期任务触发。出版权限只来自此名单。

| 邮件主题 | 行为 |
| --- | --- |
| subscribe | 真实发件地址订阅；忽略正文中的代订要求 |
| unsubscribe | 真实发件地址退订，不影响已经冻结的期刊 |
| submit | 按真实邮件 ID 收稿，保存发件人和原文 |
| publish | 编辑或 postmaster 出版，期号取触发邮件 timestamp 的 UTC 日期 |
| publish:2026-10-18 | 编辑或 postmaster 出版指定期号；重试复用冻结版本 |

主题区分大小写，不去空格。其他邮件只标读。无人订阅不调用模型、不冻结期号，投稿仍保留。有人订阅但没有稿件时冻结空期，不调用模型、不寄报；新稿使用新期号。新一期只收录未入刊稿件。模型正文空白、截断或返回工具调用时不冻结、不投递，投稿保留，可重新发出版命令。

订阅、投稿和期刊保存在成员私有 program.sqlite3。每期附真实投稿邮件 ID；附录由 Lisp 拼接，不代表摘要已经事实核验。原稿可从邮箱回查。

分发使用冻结时的名单和正文。request_id 为 edition:<期号>:<地址>；仅 accepted 且带 mail_id 的回执记为已投递。拒收或未知结果不推进成功标记，重新发送相同期号出版命令复用原键，已成功地址跳过。邮局幂等覆盖入箱后执行结果尚未记入的崩溃窗口；外部网关仍为尽力投递。

## 可选周期触发

在 ~/.tlon/cron.yaml 中声明任务，将 to 替换为实际报社成员 ID，再重启 dream：

```yaml
- name: incoming-newspaper
  to: replace-with-actual-member-id
  subject: publish
  content: 执行本报的周期出版流程。
  cron: "0 9 * * *"
```

cron 使用本地时区，报社取触发邮件的 UTC 日期，而非恢复时钟。同 UTC 日重复触发复用同一期；需要补刊可用 publish:<期号>，没有错过周期的自动补发。postmaster 不是可订阅或可投递收件人。

这不是自动事实审核或无限留存系统；成员可自由修改自己的 Lisp 采编流程。社会观测版见相邻 social-newspaper 示例。
