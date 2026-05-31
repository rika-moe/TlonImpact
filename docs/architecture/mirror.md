# mirror 进程：Wizard 配置向导

> 命令：`tlon mirror`（代码中类名为 `Wizard`）
> mirror 是一个独立的一次性进程，通过交互式菜单创建和管理 Agent 配置。
> 它不连接 dream，不依赖网络。输出是 YAML 文件。

---

## 组件结构

```
Wizard (Rich TUI)
├── menus/
│   ├── quickstart.py          — 首次运行引导
│   │     → agent_store.save() (创建默认团队)
│   ├── agent_menu.py          — Agent CRUD
│   │   ├── 创建 → agent_store.save()
│   │   ├── 编辑 → agent_store.update()
│   │   ├── 查看 → agent_store.list()
│   │   └── 删除 → agent_store.remove()
│   ├── settings_menu.py       — LLM / Docker 默认值
│   ├── diagnostics_menu.py    — 环境检查
│   │     → diagnostics.py
├── agent_store.py             — YAML 读写层
│   ├── save()   → 写入 config/agents/{id}.yaml
│   ├── load()   → 读取 config/agents/{id}.yaml
│   ├── list()   → 扫描 config/agents/*.yaml
│   └── remove() → 删除 YAML 文件
├── validators.py              — 输入校验
└── diagnostics.py             — 环境健康检查
    ├── Python 版本
    ├── Docker 守护进程
    └── API Key 有效性
```

---

## 数据流

```
用户交互 → Menu → agent_store.py
  → 写入 / 读取 config/agents/{id}.yaml
  → 重启 dream 后生效
```

Wizard 不通知 dream 配置变更。修改后的 YAML 文件在 dream（重）启动时自动加载。

---

## 文件格式

Wizard 读写 `config/agents/*.yaml`，每个 Agent 一个文件：

```yaml
agent:
  id: "dev-001"
  name: "陈代码"
  personality: "技术导向，注重细节"
  respond_to_all: false
  llm:
    model_name: "gpt-4"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

---

## 参考

- [dream.md](./dream.md) — dream 如何加载这些 YAML
- [config 设计文档](../design/config.md) — 配置系统完整说明
