# Tlon Integration Tests

> 端到端集成测试套件

---

## 快速开始

```bash
# 运行所有集成测试
./tests/integration/run_tests.sh

# 运行特定测试
./tests/integration/run_tests.sh --test TC001,TC002

# 指定端口（避免与开发环境冲突）
TLON_TEST_REST_PORT=18080 TLON_TEST_WS_PORT=18888 ./tests/integration/run_tests.sh
```

---

## 测试架构

```
tests/integration/
├── run_tests.sh          # 主测试脚本
├── test_client.py        # WebSocket 测试客户端
├── fixtures/
│   └── test_scenarios.yaml   # 测试场景定义
├── logs/                 # 服务器日志（运行时生成）
└── reports/              # 测试报告（运行时生成）
```

---

## 测试用例

| ID | 名称 | 描述 | 关键路径 |
|----|------|------|----------|
| TC001 | 基础连接测试 | Server 启动、健康检查、WebSocket 连接 | ✅ 关键 |
| TC002 | 单轮对话测试 | 发送消息并接收响应 | ✅ 关键 |
| TC003 | 多轮对话测试 | 上下文保持的连续对话 | ✅ 关键 |
| TC004 | Agent 生命周期 | wake/sleep/restart API | ✅ 关键 |
| TC005 | 多 Agent 协作 | 项目经理协调其他 Agents | ⚪ 可选 |
| TC006 | 连接恢复测试 | 断线重连 | ⚪ 可选 |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TLON_TEST_HOST` | `127.0.0.1` | Server 主机 |
| `TLON_TEST_REST_PORT` | `18080` | REST API 端口 |
| `TLON_TEST_WS_PORT` | `18888` | WebSocket 端口 |
| `OPENAI_API_KEY` | - | LLM API 密钥（必需） |

---

## 测试客户端用法

```bash
# 仅健康检查
uv run python tests/integration/test_client.py --health-check

# 运行所有测试
uv run python tests/integration/test_client.py --host 127.0.0.1 --ws-port 8888

# 运行特定测试
uv run python tests/integration/test_client.py --test TC001 --test TC002

# 生成报告
uv run python tests/integration/test_client.py --output report.json
```

---

## 调试

```bash
# 保持服务器运行（调试用）
./tests/integration/run_tests.sh --keep-server

# 然后可以手动连接
uv run python -m tlon intrude --host 127.0.0.1 --port 18888

# 查看服务器日志
tail -f tests/integration/logs/server.log
```

---

## CI/CD

集成测试在以下情况自动运行：
- 推送到 `main` 或 `develop` 分支
- 修改核心代码的 Pull Request
- 手动触发

---

*测试设计文档: [docs/developer/06-testing/integration-test-design.md](../../docs/developer/06-testing/integration-test-design.md)*
