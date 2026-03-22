# 基础层 (Base)

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 职责

提供基础设施服务：配置管理、事件系统、日志、全局设置

---

## 模块结构

```
base/
├── config/                 # 配置管理
│   ├── models.py          # 数据模型 (AgentConfig, RoleConfig)
│   ├── loader.py          # 配置加载
│   ├── saver.py           # 配置保存
│   ├── validator.py       # 配置验证
│   └── prompts.py         # Prompt 模板管理
├── events/                # 事件系统
│   ├── event.py           # Event、EventType 定义
│   └── event_bus.py       # EventBus 实现
├── logger.py              # 日志配置
└── settings.py            # 全局设置
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `ConfigLoader` | 加载 YAML 配置 | `base/config/loader.py` |
| `EventBus` | 异步事件总线 | `base/events/event_bus.py` |
| `settings` | 全局配置实例 | `base/settings.py` |

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/config.md](../../design/config.md) | 配置系统设计 | L1 |

---

## 验证清单

- [x] 配置模型与代码一致
- [x] EventBus 为单例模式
- [x] 设置可从环境变量覆盖
