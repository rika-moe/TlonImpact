# 配置系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-03-22

---

## 核心理念

1. **显式配置**: 所有配置通过 YAML 文件，无隐藏默认值
2. **分层加载**: 系统配置 → Agent 配置
3. **环境覆盖**: 敏感信息通过环境变量注入
4. **验证优先**: 启动时验证配置，拒绝无效配置

---

## 配置分层

```
system (.env) → agent (yaml)
```

| 层级 | 位置 | 内容 |
|------|------|------|
| 系统 | `.env` | API Key、日志级别、数据目录 |
| Agent | `config/agents/*.yaml` | 身份、角色、LLM 参数、Docker 配置 |

---

## 关键字段

```yaml
# Agent 配置
agent:
  id: "dev-001"           # 唯一标识
  name: "陈代码"           # 显示名称
  personality: "..."      # 性格描述
  role:                   # 角色信息（内联，无需单独配置文件）
    title: "开发工程师"
    jd: "负责系统设计、编码实现和代码审查"
    department: "技术部"
  llm:
    model_name: "gpt-4"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/domains/base.md](../architecture/domains/base.md) | 基础层架构 | L2 |
| [../reference/imports.md](../reference/imports.md) | 导入路径 | L3 |

---

## 验证清单

- [x] 配置字段与代码一致（`role` 内联在 AgentConfig 中）
- [x] YAML 格式有效
- [x] 环境变量可覆盖
