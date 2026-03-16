# Tlon Wizard 详细设计

## 1. 核心定位

**纯交互式配置工具**，通过友好的终端界面引导用户完成配置。

```
$ minions wizard

🧙‍♂️  欢迎使用 Tlon 配置向导

📋 主菜单
> 🚀 快速开始 - 初始化完整配置
  🔍 环境诊断 - 检查系统环境  
  ⚙️  配置管理 - 管理 Agent、API、存储
  🧪 测试验证 - 验证各项功能
  🗑️  重置数据 - 清理所有数据
```

## 2. 功能模块

```
🧙‍♂️ Tlon Wizard
│
├── 🚀 快速开始 (Quick Start)
│   ├── 环境检查
│   ├── API 配置
│   ├── Agent 初始化
│   └── 完成设置
│
├── 🔍 环境诊断 (Diagnose)
│   ├── 系统环境检查
│   ├── Docker 检查
│   ├── API 连接测试
│   └── 诊断报告
│
├── ⚙️ 配置管理 (Configuration)
│   ├── 🎭 Agent 管理
│   │   ├── 查看所有 Agent
│   │   ├── ➕ 添加 Agent
│   │   ├── ✏️  修改 Agent（名字/特长/职位）
│   │   └── 🗑️  删除 Agent
│   │
│   ├── 🔑 API 设置
│   │   ├── 修改 API 地址
│   │   ├── 修改 API Key
│   │   └── 切换默认模型
│   │
│   └── 💾 存储设置
│       ├── 修改数据目录
│       └── 清理历史记录
│
├── 🧪 测试验证 (Test)
│   ├── LLM 连接测试
│   ├── Docker 执行测试
│   └── Agent 启动测试
│
└── 🗑️ 重置 (Reset)
    └── 清理所有数据
```

## 3. 主菜单交互

```
$ minions wizard

🧙‍♂️  欢迎使用 Tlon 配置向导
    版本: v0.1.0

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 主菜单
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请使用方向键选择，回车确认:

> 🚀 快速开始 - 初始化完整配置（推荐首次使用）
  🔍 环境诊断 - 检查系统环境是否正常
  ⚙️  配置管理 - 管理 Agent、API 和存储设置
  🧪 测试验证 - 验证 LLM、Docker 等功能
  🗑️  重置数据 - 清理所有配置和数据（谨慎）
  ❌ 退出

[底部提示: ↑↓ 选择 | Enter 确认 | q 退出]
```

## 4. 功能详解

### 4.1 🚀 快速开始

完整的新用户引导流程：

```
[选择 🚀 快速开始]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 步骤 1/4: 环境检查
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Python 版本: 3.11.4 (满足 >=3.11)
✓ Docker: 已安装并运行 (v24.0.5)
✓ Docker Compose: 已安装
✓ 网络: 连接正常
✓ 磁盘空间: 15.2GB 可用

⚠️  Docker 镜像 ubuntu:22.04 未找到
    将在首次启动时自动拉取

按回车继续...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔑 步骤 2/4: API 配置
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请输入 LLM API 配置:

API Base URL [https://api.openai.com/v1]: 
> https://api.moonshot.cn/v1

API Key: 
> sk-********************************

选择默认模型:
  > kimik-k2.5
    gpt-4
    gpt-4-turbo
    gpt-3.5-turbo

Temperature [0.7]: 
> 0.5

✓ 正在验证 API 连接...
✓ API 连接成功! 模型 kimi-k2.5 可用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👥 步骤 3/4: Agent 配置
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择要启用的 Agent 角色:

[✓] 客服 (cs-001)
    └─ 职责: 首响应、问题分类与初步诊断
    └─ 启动模式: 常驻启动 | 响应模式: 主动+被动

[✓] 项目经理 (pm-001)  
    └─ 职责: 任务协调、进度把控、资源分配
    └─ 启动模式: 延迟启动 | 响应模式: 仅被@

[✓] 开发工程师 (dev-001)
    └─ 职责: 代码编写、Bug 修复
    └─ 启动模式: 延迟启动 | 响应模式: 仅被@

[ ] 运维工程师 (ops-001)
    └─ 职责: 系统部署、环境配置、故障处理
    └─ 启动模式: 延迟启动 | 响应模式: 仅被@

[ ] 产品经理 (prd-001)
    └─ 职责: 需求分析、功能规划、方案评审
    └─ 启动模式: 延迟启动 | 响应模式: 仅被@

使用空格选择/取消，方向键移动，回车确认

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💾 步骤 4/4: 确认创建
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

配置摘要:

┌─────────────────────────────────────────────────┐
│ API 配置                                        │
│   Base URL: https://api.moonshot.cn/v1          │
│   Model:    kimi-k2.5                           │
│   Temp:     0.5                                 │
├─────────────────────────────────────────────────┤
│ Agent 配置 (已启用 3 个)                        │
│   • 客服 - 首响应、问题分类                      │
│   • 项目经理 - 任务协调、进度把控                │
│   • 开发工程师 - 代码编写、Bug 修复              │
├─────────────────────────────────────────────────┤
│ 存储配置                                        │
│   数据目录: ./data                              │
│   记忆模式: 完整模式                            │
└─────────────────────────────────────────────────┘

确认创建以上配置? (Y/n): y

✓ 创建 .env
✓ 创建 config/agents.yaml  
✓ 创建数据目录结构
✓ 配置完成!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎉 完成!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

启动服务:
  $ minions server

启动 TUI 客户端:
  $ minions tui

按回车返回主菜单...
```

### 4.2 ⚙️ 配置管理 → 🎭 Agent 管理

Agent 管理子菜单：

```
[选择 ⚙️ 配置管理 → 🎭 Agent 管理]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎭 Agent 管理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择操作:

> 📋 查看所有 Agent - 浏览现有 Agent 配置
  ➕ 添加 Agent - 创建新的 Agent
  ✏️  修改 Agent - 修改名字、特长、职位
  🗑️  删除 Agent - 移除 Agent（谨慎）
  ← 返回上级菜单
```

#### 4.2.1 查看所有 Agent

```
[选择 📋 查看所有 Agent]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 现有 Agent 列表
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────┬────────────┬─────────────────────────┬────────┬──────────┐
│ ID       │ 名字       │ 职位/特长               │ 状态   │ 模型     │
├──────────┼────────────┼─────────────────────────┼────────┼──────────┤
│ cs-001   │ 客服小助手 │ 客户服务/首响应/问题分类 │ 常驻   │ kimi-k2.5│
│ pm-001   │ 项目经理   │ 项目管理/进度把控/协调   │ 延迟   │ kimi-k2.5│
│ dev-001  │ 开发-A     │ Python/后端/Docker      │ 延迟   │ gpt-4    │
│ ops-001  │ 运维大叔   │ Linux/部署/监控/故障处理 │ 延迟   │ kimi-k2.5│
└──────────┴────────────┴─────────────────────────┴────────┴──────────┘

提示: 使用 ✏️ 修改 Agent 来编辑详细信息

按回车返回...
```

#### 4.2.2 ➕ 添加 Agent

```
[选择 ➕ 添加 Agent]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
➕ 添加新 Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

步骤 1/4: 选择职位模板

请选择 Agent 职位:

> 🔧 开发工程师
    └─ 擅长: 代码编写、Bug 修复、代码审查
    
  🚀 运维工程师  
    └─ 擅长: 系统部署、环境配置、故障处理
    
  📋 项目经理
    └─ 擅长: 任务协调、进度把控、资源分配
    
  🎨 前端工程师
    └─ 擅长: UI实现、交互优化、样式调整
    
  📊 数据分析师
    └─ 擅长: 数据处理、可视化、统计分析
    
  📝 自定义职位
    └─ 完全自定义职位和特长

[用户选择: 🔧 开发工程师]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 2/4: 基本信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Agent 名字 [开发-B]: 
> Python开发

备注/描述 [负责代码编写和 Bug 修复]: 
> 负责 Python 后端开发和 API 设计

步骤 3/4: 特长设置

请为该 Agent 选择特长（可多选）:

通用技能:
[✓] 文件操作 - 读写文件、目录管理
[✓] Git操作 - 版本控制、分支管理

编程语言:
[✓] Python - Python 开发、数据分析
[ ] JavaScript - 前端脚本、Node.js
[ ] Go - 后端服务、高性能程序
[ ] Java - 企业级应用开发

框架/工具:
[✓] Docker - 容器化部署
[✓] FastAPI - Web API 开发
[ ] Django - Web 应用开发
[ ] SQL - 数据库操作

自定义特长: 
> 微服务架构、Redis、消息队列

步骤 4/4: 模型配置

使用模型:
  > 继承全局默认 (kimi-k2.5)
    独立配置

[用户选择: 独立配置]

模型名称: 
> gpt-4

Temperature [0.7]: 
> 0.3

Docker 内存限制 [512m]: 
> 1g

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
确认添加
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Agent 配置预览:

  名字: Python开发
  ID: dev-002
  职位: 开发工程师
  特长: 文件操作、Git操作、Python、Docker、FastAPI、微服务架构、Redis、消息队列
  模型: gpt-4 (temperature: 0.3)
  内存: 1g

确认添加? (Y/n): y

✓ 已添加到 config/agents.yaml
✓ 已创建数据目录 data/agents/dev-002/

提示: 重启 server 后新 Agent 生效
  $ minions server restart

按回车返回...
```

#### 4.2.3 ✏️ 修改 Agent（核心功能）

```
[选择 ✏️ 修改 Agent]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✏️  修改 Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择要修改的 Agent:

> 客服小助手 (cs-001)
  项目经理 (pm-001)
  开发-A (dev-001)
  运维大叔 (ops-001)
  ← 返回

[用户选择: 开发-A (dev-001)]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✏️  修改 Agent: 开发-A
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择要修改的项目:

> 🏷️  修改名字 - 当前: 开发-A
  💼 修改职位 - 当前: 开发工程师
  ⭐ 修改特长 - 当前: 文件操作、Git操作、Python、Docker
  🤖 修改模型 - 当前: gpt-4
  📝 修改描述 - 当前: 负责代码编写和 Bug 修复
  ← 返回
```

**修改名字：**

```
[选择 🏷️ 修改名字]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏷️  修改名字
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前名字: 开发-A

新名字: 
> 后端开发专家

✓ 名字已更新!

注意: 修改名字不需要重启 server
```

**修改职位：**

```
[选择 💼 修改职位]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💼 修改职位
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前职位: 开发工程师

选择新职位:

> 🔧 开发工程师
  🚀 运维工程师
  📋 项目经理
  🎨 前端工程师
  📊 数据分析师
  🛡️ 安全工程师
  📝 自定义

[用户选择: 📝 自定义]

请输入职位名称:
> 全栈工程师

请选择该职位的默认特长:
[✓] 前端开发
[✓] 后端开发
[✓] 数据库
[ ] DevOps

✓ 职位已更新为: 全栈工程师
```

**修改特长（重点功能）：**

```
[选择 ⭐ 修改特长]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⭐ 修改特长
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前特长: 文件操作、Git操作、Python、Docker

特长决定 Agent 能执行什么任务，影响其行为方式。

请选择操作:

> 从技能库选择 - 从预定义技能中选择
  自定义特长 - 输入自定义特长描述
  重置为默认 - 根据职位恢复默认特长

[用户选择: 从技能库选择]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
技能库
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择技能（可多选）:

基础技能:
[✓] 文件操作 - 读写文件、目录管理
[✓] Git操作 - 版本控制、代码提交

编程语言:
[✓] Python - Python 脚本和开发
[ ] JavaScript - 前端和后端 JS
[ ] TypeScript - 带类型的 JS
[ ] Go - Go 语言开发
[ ] Rust - Rust 系统编程
[✓] SQL - 数据库查询

框架/库:
[✓] FastAPI - Python Web 框架
[ ] Django - Python Web 框架
[ ] React - 前端 UI 框架
[ ] Vue - 前端框架

运维/工具:
[✓] Docker - 容器化
[ ] Kubernetes - 容器编排
[ ] CI/CD - 持续集成/部署
[ ] AWS - 亚马逊云服务

自定义技能:
> 机器学习、PyTorch、数据处理

[确认后]

✓ 特长已更新:
  文件操作、Git操作、Python、SQL、FastAPI、Docker、机器学习、PyTorch、数据处理

这些特长将写入 Agent 的 system prompt，影响其行为。
```

**特长说明文档：**

```
[选择 ? 了解特长如何工作]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❓ 特长是如何工作的
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

特长描述会被写入 Agent 的 "system prompt"，
告诉 Agent 它擅长什么，从而影响:

1. 任务分配建议
   当客服需要找人帮忙时，会根据特长推荐合适的 Agent

2. 工具使用倾向
   有 "Docker" 特长的 Agent 更倾向使用容器化方案

3. 解决问题的思路
   有 "安全" 特长的 Agent 会更多考虑安全因素

示例:
• "Python、FastAPI、Docker" → 后端开发任务
• "Linux、Nginx、监控" → 运维部署任务  
• "数据分析、Pandas、可视化" → 数据处理任务

按回车返回...
```

#### 4.2.4 🗑️ 删除 Agent

```
[选择 🗑️ 删除 Agent]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🗑️  删除 Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  警告: 删除 Agent 将永久删除其所有数据！

请选择要删除的 Agent:

> 开发-B (dev-002)
  测试工程师 (qa-001)
  ← 取消

[用户选择: 开发-B (dev-002)]

即将删除以下 Agent:

  名字: 开发-B
  ID: dev-002
  数据: data/agents/dev-002/ (15MB)

⚠️  此操作不可撤销！

确认删除? [输入 Agent ID 确认]: dev-002

✓ Agent dev-002 已删除
✓ 容器 agent-dev-002 已停止
✓ 数据目录已清理

按回车返回...
```

### 4.3 🔍 环境诊断

```
[选择 🔍 环境诊断]

🧙‍♂️  Tlon 环境诊断

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
系统环境
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ 操作系统: Linux (Ubuntu 22.04)
✓ Python: 3.11.4 (/usr/bin/python3)
✓ pip: 23.2.1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Docker 环境
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Docker: 24.0.5 (运行中)
✓ Docker Compose: 2.21.0
✓ 磁盘使用: 3.2GB / 50GB
⚠️  镜像 ubuntu:22.04 未找到

修复建议:
  运行 `minions server` 时会自动拉取镜像

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
配置文件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ .env 存在
✓ config/agents.yaml 存在
✓ 已配置 4 个 Agent

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API 连接
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ OPENAI_API_KEY 已设置
✓ API 连接正常 (245ms)
✓ 模型 kimi-k2.5 可用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
诊断结果: ✅ 所有检查通过
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

按回车返回主菜单...
```

### 4.4 🧪 测试验证

```
[选择 🧪 测试验证]

🧙‍♂️  Tlon 功能测试

请选择要测试的项目:

[✓] LLM 连接测试
[✓] Docker 执行测试
[ ] Agent 端到端测试

开始测试? (Y/n): y

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
测试 1/2: LLM 连接
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ API 连接成功 (245ms)
✓ 模型 kimi-k2.5 响应正常
✓ 工具调用格式正确

结果: ✅ 通过

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
测试 2/2: Docker 执行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Docker 守护进程连接正常
✓ 镜像 ubuntu:22.04 已存在
✓ 容器创建测试通过
✓ 命令执行测试通过

结果: ✅ 通过

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎉 所有测试通过!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

按回车返回...
```

### 4.5 🗑️ 重置数据

```
[选择 🗑️ 重置数据]

🧙‍♂️  重置 Tlon

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  警告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

此操作将删除以下内容:
  ✗ .env 文件
  ✗ config/agents.yaml
  ✗ data/ 目录 (所有聊天记录和 Agent 记忆)
  ✗ 所有 Docker 容器 (agent-*)

保留的内容:
  - skills/ 目录 (自定义技能)
  - 源代码

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  此操作不可撤销！

确认重置? [输入 "reset" 确认]: reset

✓ 停止所有 Agent 容器
✓ 删除数据目录
✓ 删除配置文件

系统已重置为初始状态。

运行 `minions wizard` 重新配置。
```

## 5. 配置存储结构

### 5.1 生成的 agents.yaml

```yaml
# config/agents.yaml
# 由 Tlon Wizard 生成

agents:
  # 客服
  - id: "cs-001"
    name: "客服小助手"
    role:
      name: "customer_service"
      description: "客户服务专家，负责首响应、问题分类和初步诊断"
      personality: "友好、耐心、善于沟通，专业且高效"
      proactive: true
      # 特长列表，用于任务分配和能力描述
      specialties:
        - "首响应"
        - "问题分类"
        - "用户安抚"
        - "初步诊断"
    llm:
      model_name: "kimi-k2.5"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills:
      - plan-i

  # 开发工程师
  - id: "dev-001"
    name: "Python开发"
    role:
      name: "developer"
      description: "负责 Python 后端开发和 API 设计"
      personality: "技术能力强、注重细节、喜欢钻研"
      proactive: false
      # 特长详细列表
      specialties:
        - "文件操作"
        - "Git操作"
        - "Python"
        - "Docker"
        - "FastAPI"
        - "微服务架构"
        - "Redis"
        - "消息队列"
    llm:
      model_name: "gpt-4"
      temperature: 0.3
    docker:
      image: "ubuntu:22.04"
      memory_limit: "1g"
    skills:
      - file-operations
      - git-operations
      - python-dev
```

### 5.2 特长与职位的映射关系

```python
# wizard/role_templates.py

ROLE_TEMPLATES = {
    "developer": {
        "name": "开发工程师",
        "description": "负责代码编写和 Bug 修复",
        "default_specialties": [
            "文件操作",
            "Git操作", 
            "代码审查",
        ],
        "skill_categories": ["programming", "frameworks", "databases", "tools"],
    },
    "operator": {
        "name": "运维工程师",
        "description": "负责系统部署、环境配置和故障处理",
        "default_specialties": [
            "Linux系统",
            "Docker",
            "Nginx",
            "监控告警",
        ],
        "skill_categories": ["os", "containers", "networking", "monitoring"],
    },
    "project_manager": {
        "name": "项目经理",
        "description": "负责任务协调、进度把控和资源分配",
        "default_specialties": [
            "任务分解",
            "进度把控",
            "团队协调",
            "风险管理",
        ],
        "skill_categories": ["management", "planning", "communication"],
    },
    # ... 更多角色
}

# 技能库定义
SKILL_LIBRARY = {
    "基础技能": [
        {"id": "file-ops", "name": "文件操作", "desc": "读写文件、目录管理"},
        {"id": "git", "name": "Git操作", "desc": "版本控制、分支管理"},
        {"id": "shell", "name": "Shell脚本", "desc": "Bash脚本编写"},
    ],
    "编程语言": [
        {"id": "python", "name": "Python", "desc": "Python开发"},
        {"id": "javascript", "name": "JavaScript", "desc": "前端和后端JS"},
        {"id": "typescript", "name": "TypeScript", "desc": "带类型的JS"},
        {"id": "go", "name": "Go", "desc": "Go语言开发"},
        {"id": "rust", "name": "Rust", "desc": "Rust系统编程"},
        {"id": "sql", "name": "SQL", "desc": "数据库查询"},
    ],
    "框架/库": [
        {"id": "fastapi", "name": "FastAPI", "desc": "Python Web框架"},
        {"id": "django", "name": "Django", "desc": "Python Web框架"},
        {"id": "react", "name": "React", "desc": "前端UI框架"},
        {"id": "vue", "name": "Vue", "desc": "前端框架"},
        {"id": "pandas", "name": "Pandas", "desc": "数据分析"},
    ],
    "运维/工具": [
        {"id": "docker", "name": "Docker", "desc": "容器化"},
        {"id": "k8s", "name": "Kubernetes", "desc": "容器编排"},
        {"id": "cicd", "name": "CI/CD", "desc": "持续集成/部署"},
        {"id": "aws", "name": "AWS", "desc": "亚马逊云服务"},
    ],
}
```

## 6. 技术实现

### 6.1 核心类设计

```python
# tlon/wizard/core.py

from typing import List, Dict, Optional
from dataclasses import dataclass
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import questionary

@dataclass
class AgentProfile:
    """Agent 配置数据类"""
    id: str
    name: str
    role_name: str
    description: str
    personality: str
    specialties: List[str]
    model: str
    temperature: float
    memory_limit: str
    skills: List[str]

class Wizard:
    """配置向导核心类"""
    
    def __init__(self):
        self.console = Console()
        self.config_path = Path("config/agents.yaml")
        self.env_path = Path(".env")
    
    def run(self):
        """运行主菜单"""
        self.show_banner()
        
        while True:
            choice = self.show_main_menu()
            
            if choice == "快速开始":
                self.run_quickstart()
            elif choice == "环境诊断":
                self.run_diagnose()
            elif choice == "配置管理":
                self.run_config_menu()
            elif choice == "测试验证":
                self.run_tests()
            elif choice == "重置数据":
                self.run_reset()
            else:
                break
    
    def show_main_menu(self) -> str:
        """显示主菜单"""
        return questionary.select(
            "请选择操作:",
            choices=[
                questionary.Choice("🚀 快速开始", value="快速开始"),
                questionary.Choice("🔍 环境诊断", value="环境诊断"),
                questionary.Choice("⚙️  配置管理", value="配置管理"),
                questionary.Choice("🧪 测试验证", value="测试验证"),
                questionary.Choice("🗑️  重置数据", value="重置数据"),
                questionary.Separator(),
                questionary.Choice("❌ 退出", value="退出"),
            ],
            qmark="📋",
        ).ask()
    
    def run_config_menu(self):
        """配置管理子菜单"""
        while True:
            choice = questionary.select(
                "配置管理:",
                choices=[
                    questionary.Choice("🎭 Agent 管理", value="agent"),
                    questionary.Choice("🔑 API 设置", value="api"),
                    questionary.Choice("💾 存储设置", value="storage"),
                    questionary.Separator(),
                    questionary.Choice("← 返回", value="back"),
                ],
            ).ask()
            
            if choice == "agent":
                self.run_agent_management()
            elif choice == "api":
                self.run_api_settings()
            elif choice == "storage":
                self.run_storage_settings()
            else:
                break
    
    def run_agent_management(self):
        """Agent 管理"""
        while True:
            choice = questionary.select(
                "Agent 管理:",
                choices=[
                    questionary.Choice("📋 查看所有 Agent", value="list"),
                    questionary.Choice("➕ 添加 Agent", value="add"),
                    questionary.Choice("✏️  修改 Agent", value="edit"),
                    questionary.Choice("🗑️  删除 Agent", value="delete"),
                    questionary.Separator(),
                    questionary.Choice("← 返回", value="back"),
                ],
            ).ask()
            
            if choice == "list":
                self.list_agents()
            elif choice == "add":
                self.add_agent()
            elif choice == "edit":
                self.edit_agent()
            elif choice == "delete":
                self.delete_agent()
            else:
                break
    
    def edit_agent(self):
        """修改 Agent"""
        # 1. 选择 Agent
        agents = self.load_agents()
        agent_choices = [
            questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in agents
        ]
        agent_choices.append(questionary.Choice("← 返回", value="back"))
        
        agent_id = questionary.select("选择要修改的 Agent:", choices=agent_choices).ask()
        if agent_id == "back":
            return
        
        agent = self.get_agent_by_id(agent_id)
        
        # 2. 选择修改项目
        while True:
            edit_choice = questionary.select(
                f"修改 {agent.name}:",
                choices=[
                    questionary.Choice(f"🏷️  修改名字 - 当前: {agent.name}", value="name"),
                    questionary.Choice(f"💼 修改职位 - 当前: {agent.role_name}", value="role"),
                    questionary.Choice(f"⭐ 修改特长 - 当前: {', '.join(agent.specialties[:3])}...", value="specialties"),
                    questionary.Choice(f"🤖 修改模型 - 当前: {agent.model}", value="model"),
                    questionary.Choice(f"📝 修改描述 - 当前: {agent.description[:20]}...", value="desc"),
                    questionary.Separator(),
                    questionary.Choice("← 完成", value="done"),
                ],
            ).ask()
            
            if edit_choice == "name":
                self.edit_agent_name(agent)
            elif edit_choice == "role":
                self.edit_agent_role(agent)
            elif edit_choice == "specialties":
                self.edit_agent_specialties(agent)
            elif edit_choice == "model":
                self.edit_agent_model(agent)
            elif edit_choice == "desc":
                self.edit_agent_description(agent)
            else:
                break
        
        # 保存修改
        self.save_agent(agent)
        self.console.print(f"[green]✓ Agent {agent.name} 已更新[/green]")
    
    def edit_agent_name(self, agent: AgentProfile):
        """修改 Agent 名字"""
        self.console.print(f"\n[dim]当前名字: {agent.name}[/dim]")
        
        new_name = questionary.text(
            "新名字:",
            default=agent.name,
        ).ask()
        
        if new_name and new_name != agent.name:
            old_name = agent.name
            agent.name = new_name
            self.console.print(f"[green]✓ 名字已更新: {old_name} → {new_name}[/green]")
    
    def edit_agent_role(self, agent: AgentProfile):
        """修改 Agent 职位"""
        from .templates import ROLE_TEMPLATES
        
        role_choices = [
            questionary.Choice(f"{info['name']}", value=role_id)
            for role_id, info in ROLE_TEMPLATES.items()
        ]
        role_choices.append(questionary.Choice("📝 自定义", value="custom"))
        
        new_role = questionary.select(
            f"当前职位: {agent.role_name}\n选择新职位:",
            choices=role_choices,
        ).ask()
        
        if new_role == "custom":
            custom_name = questionary.text("职位名称:").ask()
            agent.role_name = custom_name
        else:
            template = ROLE_TEMPLATES[new_role]
            agent.role_name = template["name"]
            # 询问是否更新特长
            if questionary.confirm("是否同时更新为默认特长?").ask():
                agent.specialties = template["default_specialties"].copy()
        
        self.console.print(f"[green]✓ 职位已更新为: {agent.role_name}[/green]")
    
    def edit_agent_specialties(self, agent: AgentProfile):
        """修改 Agent 特长"""
        from .templates import SKILL_LIBRARY
        
        self.console.print(f"\n[bold]当前特长:[/bold] {', '.join(agent.specialties)}")
        self.console.print("[dim]特长影响 Agent 的任务分配和行为方式\n[/dim]")
        
        # 从技能库选择
        all_skills = []
        for category, skills in SKILL_LIBRARY.items():
            self.console.print(f"\n[cyan]{category}:[/cyan]")
            for skill in skills:
                checked = skill["name"] in agent.specialties
                all_skills.append((skill["name"], checked))
        
        # 使用 checkbox 选择
        selected = questionary.checkbox(
            "选择特长 (空格选择/取消，回车确认):",
            choices=[
                questionary.Choice(name, checked=checked, value=name)
                for name, checked in all_skills
            ],
        ).ask()
        
        # 自定义特长
        custom = questionary.text(
            "添加自定义特长 (多个用逗号分隔，留空跳过):",
        ).ask()
        
        if custom:
            selected.extend([s.strip() for s in custom.split(",") if s.strip()])
        
        agent.specialties = selected
        self.console.print(f"[green]✓ 特长已更新: {', '.join(agent.specialties)}[/green]")
    
    def list_agents(self):
        """列出所有 Agent"""
        agents = self.load_agents()
        
        table = Table(title="现有 Agent 列表")
        table.add_column("ID", style="cyan")
        table.add_column("名字", style="green")
        table.add_column("职位/特长", style="yellow")
        table.add_column("启动模式", style="blue")
        table.add_column("模型", style="magenta")
        
        for agent in agents:
            mode = "常驻" if agent.proactive else "延迟"
            specialties_str = ", ".join(agent.specialties[:2]) + "..."
            table.add_row(
                agent.id,
                agent.name,
                f"{agent.role_name}\n{specialties_str}",
                mode,
                agent.model,
            )
        
        self.console.print(table)
        questionary.press_any_key_to_continue("按回车继续...").ask()
```

### 6.2 依赖

```toml
# pyproject.toml
[project.optional-dependencies]
wizard = [
    "questionary>=2.0.0",
    "rich>=13.0.0",
]
```

## 7. 入口点

```python
import click
from ..wizard.core import Wizard

@click.command()
def wizard():
    """🧙‍♂️  启动交互式配置向导"""
    w = Wizard()
    w.run()
```

```python
import click
from .cli.wizard import wizard

@click.group()
def cli():
    """Tlon - AI 多 Agent 协作系统"""
    pass

cli.add_command(wizard, name="wizard")

if __name__ == "__main__":
    cli()
```

## 8. 使用示例

```bash
# 启动 wizard
$ tlon wizard

# 主菜单 → 配置管理 → Agent 管理 → 修改 Agent
# 选择要修改的 Agent
# 选择修改项目（名字、职位、特长、模型、描述）
# 完成修改，自动保存
```
