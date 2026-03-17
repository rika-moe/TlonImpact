# 集成测试实施总结

> 完整的端到端测试方案

---

## 实施概况

**实施日期**: 2026-03-10  
**使用工具**: uv + Python + Bash + GitHub Actions  
**测试类型**: 端到端集成测试

---

## 已创建文件

```
tests/integration/
├── run_tests.sh              # 主测试脚本 (Bash)
├── test_client.py            # WebSocket 测试客户端 (Python)
├── fixtures/
│   └── test_scenarios.yaml   # 测试场景定义
├── README.md                 # 使用文档
├── logs/                     # 运行时日志目录
└── reports/                  # 测试报告目录

.github/workflows/
└── integration-tests.yml     # CI/CD 配置

docs/developer/06-testing/
├── integration-test-design.md        # 设计方案
└── integration-test-implementation.md # 本文件
```

---

## 功能特性

### 1. 测试客户端 (`test_client.py`)

- **WebSocket 连接**: 支持认证和保持连接
- **REST API 调用**: wake/sleep agent, health check
- **多轮对话**: 支持上下文连续的对话测试
- **断言系统**: 验证响应时间、内容、发送者
- **报告生成**: JSON 格式的详细测试报告

### 2. 测试脚本 (`run_tests.sh`)

- **环境检查**: uv, Python, 端口可用性
- **自动启动 Server**: 后台运行，等待就绪
- **隔离测试数据**: 独立的 config 和 data 目录
- **自动清理**: 测试结束后停止 Server，清理数据
- **参数支持**:
  ```bash
  --test TC001,TC002    # 运行特定测试
  --keep-server         # 保持 Server 运行（调试用）
  --host, --rest-port   # 自定义端口
  ```

### 3. 测试用例

| 用例 | 名称 | 验证内容 |
|------|------|----------|
| TC001 | 基础连接 | Server 启动、健康检查、WebSocket 连接 |
| TC002 | 单轮对话 | 消息发送和接收 |
| TC003 | 多轮对话 | 上下文保持的连续对话 |
| TC004 | Agent 生命周期 | wake/sleep/restart API |
| TC005 | 多 Agent 协作 | 项目经理协调能力 |
| TC006 | 连接恢复 | 断线重连 |

### 4. CI/CD 集成

GitHub Actions 工作流：

```yaml
触发条件:
  - push 到 main/develop 分支
  - 修改核心代码的 Pull Request
  - 手动触发

任务:
  1. integration-test: 完整测试套件
  2. smoke-test: PR 快速健康检查

Artifacts:
  - integration-test-report: JSON 测试报告
  - server-logs: 失败时的服务器日志
```

---

## 使用示例

### 本地运行

```bash
# 基本用法
./tests/integration/run_tests.sh

# 指定端口（避免冲突）
TLON_TEST_REST_PORT=18080 TLON_TEST_WS_PORT=18888 ./tests/integration/run_tests.sh

# 只运行关键测试
./tests/integration/run_tests.sh --test TC001,TC002

# 调试模式（保持 Server 运行）
./tests/integration/run_tests.sh --keep-server

# 然后可以手动连接调试
uv run python -m tlon intrude --host 127.0.0.1 --port 18888
```

### 直接运行测试客户端

```bash
# 健康检查
uv run python tests/integration/test_client.py --health-check

# 完整测试
uv run python tests/integration/test_client.py \
    --host 127.0.0.1 \
    --ws-port 8888 \
    --rest-port 8080 \
    --output report.json

# 特定测试
uv run python tests/integration/test_client.py --test TC001 --test TC002
```

---

## 输出示例

### 控制台输出

```
🧪 Tlon Integration Test Client
   Server: 127.0.0.1:18888
   REST: 127.0.0.1:18080

Checking server health...
✅ Server healthy: 0.2.0

============================================================
Running TC001: 基础连接测试
============================================================
✅ TC001 PASSED (5234ms)

============================================================
Running TC002: 单轮对话测试
============================================================
✅ TC002 PASSED (12450ms)

============================================================
TEST SUMMARY
============================================================
Total:   4
Passed:  4 ✅
Failed:  0 ❌
Skipped: 0 ⏭️
Duration: 45.2s

Report saved to: tests/integration/reports/test_report_20260310_143000.json
```

### JSON 报告

```json
{
  "test_run": {
    "timestamp": "2026-03-10T14:30:00Z",
    "duration_ms": 45200,
    "server_version": "0.2.0"
  },
  "summary": {
    "total": 4,
    "passed": 4,
    "failed": 0,
    "skipped": 0
  },
  "results": [
    {
      "id": "TC001",
      "name": "基础连接测试",
      "status": "passed",
      "duration_ms": 5234
    }
  ],
  "logs": {}
}
```

---

## 架构说明

### 测试流程

```
1. 检查环境
   ├── uv 安装
   ├── Python 可用
   ├── OPENAI_API_KEY 设置
   └── 端口可用性

2. 设置环境
   ├── 创建临时数据目录
   ├── 复制测试配置
   └── 创建日志/报告目录

3. 启动 Server
   ├── 后台启动 tlon dream
   ├── 轮询 /health 检查就绪
   └── 超时: 30 秒

4. 运行测试
   ├── TC001: 连接测试
   ├── TC002: 单轮对话
   ├── TC003: 多轮对话
   └── TC004: Agent 生命周期

5. 生成报告
   ├── JSON 格式
   ├── 包含时间戳、结果、日志
   └── 控制台摘要

6. 清理
   ├── 停止 Server
   ├── 可选: 清理临时数据
   └── 可选: 保持 Server 运行
```

### 组件关系

```
run_tests.sh (Bash)
    ├── test_client.py (Python)
    │       ├── WebSocket 连接
    │       ├── REST API 调用
    │       ├── 断言验证
    │       └── 报告生成
    ├── fixtures/test_scenarios.yaml
    │       └── 测试场景定义
    └── logs/ + reports/
```

---

## 后续扩展建议

### 1. Mock LLM 支持

添加 `--mock-llm` 选项，使用预设响应代替真实 LLM 调用：
- 更快的测试执行
- 不消耗 API 配额
- 确定性响应便于断言

### 2. 性能基准测试

添加性能测试用例：
- 响应时间基准
- 并发客户端压力测试
- 内存使用监控

### 3. 场景扩展

在 `test_scenarios.yaml` 中添加更多场景：
- 错误处理场景
- 边界条件测试
- 长对话压力测试

### 4. 可视化报告

生成 HTML 测试报告：
- 交互式结果展示
- 时间线图表
- 覆盖率可视化

---

## 注意事项

1. **API 配额**: 集成测试消耗 LLM API，建议在 CI 中设置限制或定时触发
2. **端口冲突**: 默认使用 18080/18888 避免与开发环境冲突
3. **超时设置**: LLM 调用可能需要 30-60 秒，确保 CI timeout 足够
4. **并发执行**: 同一时间只能运行一个集成测试实例

---

*实施完成于 2026-03-10*
