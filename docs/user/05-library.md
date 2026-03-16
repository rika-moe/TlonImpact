> ⚠️ **注意**: Skill 系统当前实现已废弃，计划重新设计。本章内容可能过时。

# 第五章：巴别图书馆

> 六边形的无限空间，包含所有可能的技能之书。

---

## 你不需要什么都会

传统开发：你需要学会 Git、Docker、各种 API、测试工具……

Tlon 开发：你只需要知道**有什么书**，让 Agents 去**借阅**。

当你的 Agent 需要一项新能力时，它会去图书馆取一本书。不是阅读——是**成为**那本书。

---

## 一次借阅的旅程

```
> 分析一下这个 PDF 的内容

[陈代码] 我需要 PDF 处理能力。
         正在去图书馆...
         
         [系统] Agent 陈代码 正在激活 Skill：pdf-processing
         
[陈代码] [已获取技能：pdf-processing]
         
         现在我可以读取这个 PDF 了。
         [执行：python ./skills/pdf-processing/scripts/extract.py]
         
         内容摘要：这是一个关于...
```

注意这个过程：
1. Agent 识别出需要的能力
2. 系统去图书馆取书（激活 Skill）
3. Agent "成为"了那个技能的使用者
4. 完成任务后，书归还（可选，也可以保留）

---

## 图书馆的运作

### 第一层：发现（Discovery）

当你启动 Tlon，系统会扫描 `skills/` 目录，登记所有可借阅的书：

```
[系统] 正在发现技能...
       - git-operations: Git 版本控制操作
       - file-operations: 文件读写和管理
       - web-search: 网络搜索和信息获取
       - pdf-processing: PDF 文本提取和处理
       - docker-operations: Docker 容器管理
       
       共发现 5 本技能之书。
```

此时 Agents 只知道**书名和简介**，还没有读内容。

### 第二层：借阅（Activation）

当任务需要时，Agent 会借阅（激活）那本书：

```
[陈代码] 我需要使用 Git。
         [激活 Skill：git-operations]
         
         （现在陈代码的上下文中加载了完整的 SKILL.md）
```

此时 Agent 掌握了：
- 该技能的详细用法
- 可用的脚本和工具
- 最佳实践和注意事项

### 第三层：使用（Execution）

借阅后，Agent 可以：

1. **执行脚本**：
   ```
   [陈代码] [执行：./skills/git-operations/scripts/commit.sh]
   ```

2. **读取参考**：
   ```
   [陈代码] [查阅：git-operations/references/advanced-usage.md]
   ```

3. **使用模板**：
   ```
   [陈代码] [加载模板：git-operations/assets/pr-template.md]
   ```

---

## 当前可借阅的书

### 基础技能

| 技能名 | 用途 | 示例 |
|-------|------|------|
| **git-operations** | 版本控制 | `git commit`, `git branch`, `git merge` |
| **file-operations** | 文件管理 | 读写、移动、压缩、解压 |
| **docker-operations** | 容器管理 | 镜像构建、容器启停、日志查看 |

### 数据处理

| 技能名 | 用途 | 示例 |
|-------|------|------|
| **pdf-processing** | PDF 处理 | 文本提取、表格解析、合并拆分 |
| **csv-processing** | CSV 处理 | 读取、过滤、转换、统计 |
| **json-processing** | JSON 处理 | 查询、转换、验证 |

### 网络与信息

| 技能名 | 用途 | 示例 |
|-------|------|------|
| **web-search** | 网络搜索 | 查询文档、寻找解决方案 |
| **http-client** | HTTP 请求 | API 调用、Webhook 测试 |

### 开发工具

| 技能名 | 用途 | 示例 |
|-------|------|------|
| **code-linting** | 代码检查 | ESLint, Pylint, 自动格式化 |
| **test-runner** | 测试执行 | 单元测试、集成测试 |
| **documentation** | 文档生成 | API 文档、README 生成 |

---

## 如何添加新书

你可以自己为图书馆添加技能。

### 目录结构

```
skills/
└── my-skill/                    # 技能目录名
    ├── SKILL.md                 # 必需：技能定义
    ├── scripts/                 # 可选：可执行脚本
    │   ├── helper.py
    │   └── utils.sh
    ├── references/              # 可选：参考文档
    │   └── api-reference.md
    └── assets/                  # 可选：模板和资源
        └── template.json
```

### SKILL.md 格式

```markdown
---
name: my-skill
description: 一句话描述这个技能是做什么的
---

# My Skill

## 什么时候使用
描述适用场景...

## 如何使用
详细指令...

## 可用脚本
- `scripts/helper.py`: 用途说明

## 示例
具体的使用示例...
```

### 示例：自定义技能

创建一个发送邮件的技能：

**skills/email-sender/SKILL.md：**

```markdown
---
name: email-sender
description: 发送邮件，支持 SMTP 和模板
---

# Email Sender

## 什么时候使用
当需要发送通知邮件、报告或提醒时。

## 如何使用
1. 检查是否有 SMTP 配置
2. 使用 scripts/send.py 发送
3. 支持 HTML 和纯文本格式

## 可用脚本
- `scripts/send.py`: 发送邮件
  参数：--to, --subject, --body, --html

## 示例
```bash
./skills/email-sender/scripts/send.py \
  --to "user@example.com" \
  --subject "项目进度报告" \
  --body "本周完成了..."
```
```

**skills/email-sender/scripts/send.py：**

```python
#!/usr/bin/env python3
import argparse
import smtplib
from email.mime.text import MIMEText

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--to', required=True)
    parser.add_argument('--subject', required=True)
    parser.add_argument('--body', required=True)
    args = parser.parse_args()
    
    # 发送逻辑...
    print(f"邮件已发送至 {args.to}")

if __name__ == "__main__":
    main()
```

添加后重启 Tlon，新技能就会被发现。

---

## 借阅的代价

每次借阅（激活）Skill，会：

1. **增加上下文长度**：SKILL.md 的内容进入 Agent 的上下文
2. **占用 Token**：LLM 调用成本增加
3. **可能冲突**：多个 Skills 的指令可能互相干扰

所以系统采用**按需借阅**：
- 不需要时，不加载
- 用完后，可以归还（deactivate）
- 常用技能，可以保持借阅状态

---

## 下一步

- [第六章：镜子](06-mirror.md) - 创造新的意识
- [第七章：花园](07-garden.md) - 时间的分叉

---

> **图书馆管理员的话**
> 
> "这座图书馆包含所有可能的书籍。
> 但找到那本你需要的，需要智慧。
> 
> 幸运的是，你的 Agents 知道如何寻找。"
