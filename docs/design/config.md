# 配置系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 核心理念

1. **显式配置**: 所有配置通过 YAML 文件，无隐藏默认值
2. **Narrative 驱动**: `identity`/`profile`/`behavior` 各是一段自由文本，非字段集合
3. **分层加载**: 系统配置 → Agent 配置
4. **环境覆盖**: 敏感信息通过环境变量注入
5. **验证优先**: 启动时验证配置，拒绝无效配置

---

## 配置分层

```
system (.env) → agent (yaml)
```

| 层级 | 位置 | 内容 |
|------|------|------|
| 系统 | `.env` | API Key、日志级别、数据目录 |
| Agent | `config/agents/*.yaml` | identity、profile、behavior、LLM 参数、Docker 配置 |

---

## 关键字段

```yaml
# Agent 配置
agent:
  id: "dev-001"           # 唯一标识（系统路由键）
  name: "林知遥"           # 显示名称

  identity: |             # 自我身份：你是谁、性格特点
    你是一名技术能力强的开发工程师...

  profile: |              # 公开档案：职责、能力、何时可以@你
    开发工程师，技术部。负责代码编写...

  behavior: |             # 行为守则：工作流程、响应规则
    收到@时响应。被@时先分析...

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
| [narrative-prompt.md](./narrative-prompt.md) | Prompt 架构设计（narrative-driven） | L2 |
| [architecture/domains/base.md](../architecture/domains/base.md) | 基础层架构 | L2 |
| [../reference/imports.md](../reference/imports.md) | 导入路径 | L3 |

---

## 验证清单

- [x] 配置字段与代码一致（`identity`/`profile`/`behavior`）
- [x] `RoleConfig` 已移除，role 信息合并入 narrative 文本
- [x] YAML 格式有效
- [x] 环境变量可覆盖
