# Tlon CLI 开发路线图

> 基于 `docs/developer/cli/command-design.md` 设计方案的实施计划

---

## 架构参考

本路线图涉及的实现位于整体架构的 **入口层** 和 **交互层**：

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 入口层 (Entry)        │ __main__.py, cli/main.py  ← 本路线图 │
├─────────────────────────────────────────────────────────────┤
│ 2. 交互层 (Adapters)     │ CLI, TUI  ← 本路线图                │
├─────────────────────────────────────────────────────────────┤
│ 3. 服务端层 (Server)     │ REST API, WebSocket, TlonServer     │
├─────────────────────────────────────────────────────────────┤
│ 4. 组织层 (Organization) │ EventBus, Congress, Positions       │
│    (原核心层 Core)       │ 注：即将改名为组织层                 │
├─────────────────────────────────────────────────────────────┤
│ 5. Agent 层              │ Agent, LLMClient, DockerRuntime     │
├─────────────────────────────────────────────────────────────┤
│ 6. 子系统层 (Subsystems) │ Skills, Memory, Tasks               │
├─────────────────────────────────────────────────────────────┤
│ 7. 工具层 (Tools)        │ Tools 集合                           │
├─────────────────────────────────────────────────────────────┤
│ 8. 🔒 Sandbox 层         │ Docker Container (per Agent)        │
└─────────────────────────────────────────────────────────────┘
```

完整架构图参见：[architecture-with-sandbox.md](../architecture-with-sandbox.md)

---

## 任务总览

| Task | 描述 | 优先级 | 状态 | 依赖 |
|-----|------|--------|------|------|
| 1 | 重构 CLI 入口结构 | P0 | ✅ 已完成 | - |
| 2 | 实现 `dream` 命令 | P0 | ✅ 已完成 | Task 1 |
| 3 | 实现 `mirror` 命令基础 | P0 | ✅ 已完成 | Task 1 |
| 4 | 实现 `intrude` 命令基础 | P0 | ✅ 已完成 | Task 1 |
| 5 | 实现 `intrude tui` 子命令 | P1 | ✅ 已完成 | Task 4 |
| 6 | 实现 `intrude web` 子命令 | P1 | ✅ 已完成 | Task 4 |
| 7 | 实现 `intrude telegram` 子命令 | P2 | ✅ 已完成 | Task 4 |
| 8 | 实现 `intrude void` 子命令 | P2 | ✅ 已完成 | Task 4 |
| 9 | 完善帮助信息和文学化输出 | P1 | ✅ 已完成 | Task 2-4 |
| 10 | 测试与验证 | P0 | ✅ 已完成 | Task 2-9 |

---

## Task 1: 重构 CLI 入口结构

**目标**：将现有 CLI 重构为新的命令结构，支持 `dream`/`mirror`/`intrude` 三个主命令

**具体工作**：
1. 修改 `src/tlon/cli/main.py`，创建新的命令解析结构
2. 保留现有 `server`/`tui`/`wizard` 命令作为别名（向后兼容）
3. 添加新的主命令组：dream, mirror, intrude
4. 确保 `--version` 和 `--help` 正常工作

**验收标准**：
- `tlon --help` 显示新的命令结构
- 旧命令（server/tui/wizard）仍然可用
- 新命令结构已定义但未完全实现

**预计文件变更**：
- `src/tlon/cli/main.py` - 重构命令解析

---

## Task 2: 实现 `dream` 命令

**目标**：实现 `tlon dream` 命令启动 Server

**具体工作**：
1. 实现 `dream` 命令，调用 `src/tlon/server/app.py` 启动服务
2. 支持 `--lucid` / `-l` 参数启用 debug 日志
3. 添加文学化启动输出：
   ```
   [梦境开始] 特隆正在入侵现实...
              Server: ws://localhost:8765
              代表大会: 等待连接
              Agents: 休眠中
   ```
4. 确保与现有 `tlon server` 行为一致

**验收标准**：
- `tlon dream` 正常启动 WebSocket Server
- `tlon dream --lucid` 启用 DEBUG 级别日志
- 启动输出包含文学化文案

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 dream 命令实现

---

## Task 3: 实现 `mirror` 命令基础

**目标**：实现 `tlon mirror` 命令基础框架

**具体工作**：
1. 实现 `mirror` 命令调用配置向导（复用现有 wizard）
2. 实现 `mirror --reflect` / `-r` 列出所有 Agents
3. 预留 `--create`, `--destroy`, `--edit` 参数接口（后续实现）
4. 添加文学化输出：
   ```
   你站在镜前，镜中的轮廓在等待。
   
   名字：_ _ _ _ _ _ _ _
   ...
   ```

**验收标准**：
- `tlon mirror` 启动交互式配置向导
- `tlon mirror -r` 显示 Agents 列表
- 与现有 `tlon wizard` 行为一致

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 mirror 命令实现

---

## Task 4: 实现 `intrude` 命令基础

**目标**：实现 `tlon intrude` 命令基础框架

**具体工作**：
1. 实现 `intrude` 命令解析子命令（tui/web/telegram/void）
2. 默认行为为启动 TUI
3. 检查 `dream` 是否运行，未运行则提示用户
4. 建立 WebSocket 连接到 Server
5. 添加文学化连接输出：
   ```
   正在突破现实边界...
   连接至 ws://localhost:8765 ... 成功
   
   你已进入 代表大会
   ...
   ```

**验收标准**：
- `tlon intrude` 默认启动 TUI
- 能正确检测 Server 是否运行
- 显示文学化连接文案

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 intrude 命令基础

---

## Task 5: 实现 `intrude tui` 子命令

**目标**：实现 TUI 界面连接

**具体工作**：
1. 实现 `intrude tui` 调用现有 TUI 客户端
2. 复用 `src/tlon/cli/tui_simple.py` 的实现
3. 确保与 Server 的 WebSocket 连接正常

**验收标准**：
- `tlon intrude tui` 正常启动 TUI
- 能与 Server 正常通信
- 功能与现有 `tlon tui` 一致

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 tui 子命令

---

## Task 6: 实现 `intrude web` 子命令

**目标**：实现 Web 界面连接

**具体工作**：
1. 实现 `intrude web` 命令
2. 启动 Web 服务器或打开浏览器
3. 连接 WebSocket Server
4. （可选）在终端显示访问地址

**验收标准**：
- `tlon intrude web` 启动 Web 界面
- 能正常连接 Server

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 web 子命令
- 可能需要 Web 客户端相关文件

---

## Task 7: 实现 `intrude telegram` 子命令

**目标**：实现 Telegram Bot 连接

**具体工作**：
1. 实现 `intrude telegram` 命令
2. 启动 Telegram Bot 轮询
3. 连接 WebSocket Server

**验收标准**：
- `tlon intrude telegram` 启动 Telegram Bot
- Bot 能接收消息并转发到 Server

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 telegram 子命令

---

## Task 8: 实现 `intrude void` 子命令

**目标**：实现静默模式连接

**具体工作**：
1. 实现 `intrude void` 命令
2. 仅建立 WebSocket 连接，无 UI
3. 监听事件并输出到日志
4. 支持接收通知

**验收标准**：
- `tlon intrude void` 静默连接 Server
- 后台运行，仅记录日志
- 能接收系统通知

**预计文件变更**：
- `src/tlon/cli/main.py` - 添加 void 子命令

---

## Task 9: 完善帮助信息和文学化输出

**目标**：完善所有命令的帮助信息和文学化输出

**具体工作**：
1. 实现无参数时的文学化帮助：
   ```
   🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

   Commands:
     dream       维持梦境（启动 Server）
     mirror      镜中造物（配置 Agent）
     intrude     入侵现实（连接界面）
   ...
   ```
2. 每个命令添加 `--help` 文学化说明
3. 统一输出风格和术语
4. 更新 `docs/developer/cli/quick-reference.md`（如有变化）

**验收标准**：
- `tlon` 无参数显示文学化帮助
- 每个命令 `--help` 包含文学化描述
- 输出风格与品牌叙事一致

**预计文件变更**：
- `src/tlon/cli/main.py` - 更新帮助信息
- `docs/developer/cli/quick-reference.md` - 如有变化则更新

---

## Task 10: 测试与验证

**目标**：全面测试 CLI 命令

**具体工作**：
1. 测试每个命令的基本功能
2. 测试参数组合
3. 测试错误处理
4. 验证文学化输出正确显示
5. 更新设计文档中的检查清单

**验收标准**：
- 所有检查清单项完成
- 无阻塞性 bug
- 文档与实现一致

**预计文件变更**：
- `docs/developer/cli/command-design.md` - 更新检查清单状态

---

## 附录：依赖关系图

```
Task 1 (重构 CLI 入口)
    │
    ├──→ Task 2 (dream)
    │
    ├──→ Task 3 (mirror)
    │
    └──→ Task 4 (intrude 基础)
            │
            ├──→ Task 5 (intrude tui)
            │
            ├──→ Task 6 (intrude web)
            │
            ├──→ Task 7 (intrude telegram)
            │
            └──→ Task 8 (intrude void)

Task 9 (完善帮助) 依赖 Task 2,3,4
Task 10 (测试) 依赖 Task 2-9
```

---

## 当前状态

**进度**: 10/10 tasks ✅

**状态**: 所有任务已完成

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [cli/command-design.md](./cli/command-design.md) | CLI 命令设计规范 |
| [cli/tui_spec.md](./cli/tui_spec.md) | TUI 功能规约 |
| [cli/implementation-summary.md](./cli/implementation-summary.md) | CLI 实施总结 |
| [06-testing/integration-test-design.md](./06-testing/integration-test-design.md) | 集成测试设计 |

---

*创建日期：2026-03-09*
*基于设计：docs/developer/cli/command-design.md*
