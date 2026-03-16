# 代码风格规范

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **一致性优先**: 优先遵循项目现有风格
2. **工具自动化**: 使用 Black、Ruff 自动化格式化
3. **类型安全**: 严格类型检查，无隐式 Any

---

## 工具配置

### Black（格式化）

```bash
black src/ tests/        # 格式化
black --check src/      # 检查
```

配置 (`pyproject.toml`):
```toml
[tool.black]
line-length = 100
target-version = ["py311"]
```

### Ruff（代码检查）

```bash
ruff check src/ tests/       # 检查
ruff check --fix src/       # 自动修复
```

配置:
```toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = ["E", "W", "F", "I", "N", "UP", "B", "C4", "SIM"]
ignore = ["E501"]
```

### mypy（类型检查）

```bash
mypy src/tlon
```

严格模式:
- `disallow_untyped_defs = true`
- `disallow_incomplete_defs = true`
- `no_implicit_optional = true`

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../reference/imports.md](../reference/imports.md) | 导入路径规范 | L3 |

---

## 验证清单

- [x] pyproject.toml 配置有效
- [x] 工具版本锁定
