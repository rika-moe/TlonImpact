# Tlon 集成测试设计方案

> 模拟真实使用场景，验证端到端功能

---

## 一、测试目标

集成测试验证以下端到端场景：

1. **服务启动** - Server 能正常启动并加载配置
2. **客户端连接** - TUI 客户端能连接 WebSocket
3. **消息流转** - 用户消息能到达 Agents，Agent 响应能返回
4. **Agent 生命周期** - wake/sleep/restart 命令正常工作
5. **多轮对话** - 支持上下文连续的对话
6. **错误恢复** - 连接断开后能重连

---

## 二、测试架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Integration Test Harness                  │
│                     (tests/integration/run_tests.sh)             │
└──────────────────────────────┬────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  Server Process │  │  Test Client     │  │  Health Monitor  │
│  (tlon dream)   │  │  (WebSocket)     │  │  (REST API)      │
│                 │  │                  │  │                  │
│  - REST API     │◄─┤  - Send messages │  │  - Check status  │
│  - WebSocket    │  │  - Receive resp  │  │  - Verify agents │
│  - Event Bus    │  │  - Assert results│  │  - Collect logs  │
└─────────────────┘  └──────────────────┘  └──────────────────┘
```

---

## 三、测试组件

### 3.1 测试客户端 (`test_client.py`)

非交互式的 WebSocket 客户端，用于：
- 连接 Server
- 发送预定义消息序列
- 接收并记录响应
- 执行断言验证

### 3.2 测试脚本 (`run_tests.sh`)

Bash 脚本负责：
- 启动 Server（后台）
- 等待服务就绪
- 运行测试用例
- 收集结果
- 清理进程

### 3.3 健康检查器 (`health_check.py`)

验证服务状态：
- 检查 /health 端点
- 验证 Agents 加载
- 检查 WebSocket 可连接

---

## 四、测试用例设计

### 测试用例 1: 基础连接测试 (TC001)

**目的**: 验证 Server 启动和客户端连接

**步骤**:
1. 启动 Server
2. 等待健康检查通过
3. 连接 WebSocket
4. 发送认证消息
5. 验证连接成功

**预期结果**:
- Server 在 10 秒内启动
- 健康检查返回 200
- WebSocket 连接成功
- 认证通过

---

### 测试用例 2: 单轮对话测试 (TC002)

**目的**: 验证消息发送和接收

**步骤**:
1. 连接 WebSocket
2. 发送消息: "你好"
3. 等待客服 Agent 响应
4. 记录响应时间和内容

**预期结果**:
- 消息发送成功
- 收到客服 Agent 的问候响应
- 响应时间 < 30 秒（LLM 调用）

---

### 测试用例 3: 多轮对话测试 (TC003)

**目的**: 验证上下文保持

**步骤**:
1. 开始对话: "我要做一个读书笔记网站"
2. 等待项目经理响应
3. 发送跟进: "用什么技术栈比较好？"
4. 等待技术建议
5. 发送: "就按这个方案做"

**预期结果**:
- 每轮都有响应
- 响应内容相关且连贯
- Agents 能识别上下文

---

### 测试用例 4: Agent 控制测试 (TC004)

**目的**: 验证 wake/sleep/restart 命令

**步骤**:
1. 检查初始 Agent 状态
2. 调用 /wake lin-zhiyao
3. 验证状态变为 active
4. 调用 /sleep lin-zhiyao
5. 验证状态变为 standby
6. 调用 /restart lin-zhiyao
7. 验证状态变化序列

**预期结果**:
- 所有 API 调用返回 200
- 状态变化正确
- 操作响应时间 < 5 秒

---

### 测试用例 5: 多 Agent 协作测试 (TC005)

**目的**: 验证 Agents 之间的协作

**步骤**:
1. 发送: "@项目经理 安排一个全栈项目"
2. 等待项目经理协调
3. 验证其他 Agents 被 @ 并响应
4. 观察任务分配过程

**预期结果**:
- 项目经理发出协调消息
- 被 @ 的 Agents 响应
- 协作流程完整

---

### 测试用例 6: 错误恢复测试 (TC006)

**目的**: 验证连接断开后的恢复

**步骤**:
1. 建立连接
2. 断开网络（模拟）
3. 等待断线检测
4. 重新连接
5. 验证能继续对话

**预期结果**:
- 断线被正确检测
- 重连成功
- 新消息能正常收发

---

### 测试用例 7: 并发客户端测试 (TC007)

**目的**: 验证多客户端同时连接

**步骤**:
1. 启动 Server
2. 同时连接 3 个测试客户端
3. 每个客户端发送消息
4. 验证所有客户端收到广播

**预期结果**:
- 所有客户端连接成功
- 消息广播到所有客户端
- 无消息丢失

---

## 五、测试数据

### 测试消息序列

```yaml
# tests/integration/fixtures/test_scenarios.yaml
scenarios:
  - name: "基础问候"
    messages:
      - role: user
        content: "你好"
        expect_response_from: ["customer_service"]
        timeout: 30

  - name: "需求分析"
    messages:
      - role: user
        content: "我要做一个读书笔记网站"
        expect_response_from: ["project_manager"]
        timeout: 45
      - role: user
        content: "需要什么技术栈？"
        expect_mention: ["developer"]
        timeout: 45
      - role: user
        content: "就按这个方案做"
        expect_any_response: true
        timeout: 30

  - name: "技术咨询"
    messages:
      - role: user
        content: "@developer 怎么实现用户认证？"
        expect_response_from: ["developer"]
        timeout: 45
```

---

## 六、执行流程

```bash
# 1. 启动测试环境
./tests/integration/run_tests.sh

# 2. 执行流程:
#    a. 检查环境（uv, python, 端口可用性）
#    b. 创建隔离数据目录
#    c. 启动 Server（后台）
#    d. 等待健康检查（轮询 /health，最多 30 秒）
#    e. 顺序执行测试用例 TC001-TC007
#    f. 生成测试报告
#    g. 停止 Server
#    h. 清理临时数据
```

---

## 七、成功/失败判定

### 成功标准
- 所有关键测试用例通过（TC001-TC004）
- Server 能正常启动和关闭
- 无未处理的异常

### 警告（非阻塞）
- 可选测试用例失败（TC005-TC007）
- 响应时间超过预期但未超时
- LLM 返回质量警告

### 失败标准
- 关键测试用例失败
- Server 崩溃或无法启动
- 测试断言失败

---

## 八、报告格式

```json
{
  "test_run": {
    "timestamp": "2026-03-10T14:30:00Z",
    "duration_ms": 125000,
    "server_version": "0.2.0"
  },
  "summary": {
    "total": 7,
    "passed": 6,
    "failed": 1,
    "skipped": 0
  },
  "results": [
    {
      "id": "TC001",
      "name": "基础连接测试",
      "status": "passed",
      "duration_ms": 5234
    },
    {
      "id": "TC002",
      "name": "单轮对话测试",
      "status": "failed",
      "duration_ms": 35000,
      "error": "Timeout waiting for response"
    }
  ],
  "logs": {
    "server": "path/to/server.log",
    "client": "path/to/client.log"
  }
}
```

---

## 九、CI/CD 集成

### GitHub Actions 示例

```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  integration-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Install uv
        uses: astral-sh/setup-uv@v1
      
      - name: Run integration tests
        run: |
          export OPENAI_API_KEY=${{ secrets.OPENAI_API_KEY }}
          ./tests/integration/run_tests.sh
      
      - name: Upload test report
        uses: actions/upload-artifact@v3
        with:
          name: integration-test-report
          path: tests/integration/reports/
```

---

## 十、本地运行

```bash
# 快速运行
./tests/integration/run_tests.sh

# 指定端口（避免冲突）
REST_PORT=18080 WS_PORT=18888 ./tests/integration/run_tests.sh

# 只运行特定测试
./tests/integration/run_tests.sh --test TC001,TC002

# 保持 Server 运行（调试用）
./tests/integration/run_tests.sh --keep-server

# 使用 mock LLM（不消耗 API）
./tests/integration/run_tests.sh --mock-llm
```

---

*设计日期: 2026-03-10*
*状态: 设计完成，待实施*
