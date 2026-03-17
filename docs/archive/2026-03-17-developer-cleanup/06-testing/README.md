# 测试体系

> Tlon 的测试策略与实现

---

## 测试层次

```
┌─────────────────────────────────────────────────────────────────┐
│                      集成测试 (Integration)                       │
│              端到端测试，验证完整用户场景                          │
│         tests/integration/ | docs/developer/06-testing/           │
├─────────────────────────────────────────────────────────────────┤
│                      单元测试 (Unit)                              │
│              模块级测试，验证单个组件                              │
│                    tests/config/ | tests/wizard/                  │
├─────────────────────────────────────────────────────────────────┤
│                      静态检查 (Static)                            │
│              类型检查、代码风格、安全检查                          │
│                    mypy | ruff | black                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 测试类型

### 1. 单元测试

**范围**: 单个模块、函数、类  
**工具**: pytest  
**位置**: `tests/`  
**运行**:
```bash
uv run python -m pytest tests/ -v
```

**覆盖模块**:
- `tests/config/` - 配置系统测试
- `tests/wizard/` - 配置向导测试
- `tests/tasks/` - 任务系统测试
- `tests/test_skills/` - 技能系统测试

### 2. 集成测试

**范围**: 端到端场景，Server + Client  
**工具**: 自定义测试客户端 + Bash 脚本  
**位置**: `tests/integration/`  
**运行**:
```bash
./tests/integration/run_tests.sh
```

**覆盖场景**:
- Server 启动和连接
- 单轮/多轮对话
- Agent 生命周期控制
- 多 Agent 协作
- 连接恢复

**详见**: [集成测试详细文档](./integration-test-design.md)

---

## 快速参考

### 运行所有测试

```bash
# 单元测试
uv run python -m pytest tests/ -v

# 集成测试
./tests/integration/run_tests.sh

# 指定端口运行集成测试
TLON_TEST_REST_PORT=18080 TLON_TEST_WS_PORT=18888 ./tests/integration/run_tests.sh
```

### 代码质量检查

```bash
# 格式化
uv run black src/ tests/

# 代码检查
uv run ruff check src/ tests/

# 类型检查
uv run mypy src/tlon
```

---

## 文档索引

| 文档 | 内容 | 位置 |
|------|------|------|
| [integration-test-design.md](./integration-test-design.md) | 集成测试设计方案 | `docs/developer/06-testing/` |
| [integration-test-implementation.md](./integration-test-implementation.md) | 集成测试实施总结 | `docs/developer/06-testing/` |

---

## CI/CD 集成

测试在以下情况自动运行：

1. **Pull Request**: 运行单元测试 + Smoke 测试
2. **Push to main/develop**: 运行完整测试套件
3. **手动触发**: 可指定测试用例

**配置**: `.github/workflows/`

---

*最后更新: 2026-03-10*
