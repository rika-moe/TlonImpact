# 调试与故障排除

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **日志优先**: 通过结构化日志定位问题
2. **数据可观测**: 所有状态持久化，可离线分析
3. **容器透明**: Docker 容器状态可查询

---

## 调试方法

### 启用调试日志

```bash
tlon server --debug
# 或
DEBUG=true tlon server
```

### 查看 Agent 记忆

记忆存储在：`data/agents/{agent_id}/memory/`

| 文件 | 内容 |
|------|------|
| `config.json` | 记忆配置 |
| `working/interactions.jsonl` | 工作记忆 |
| `episodic/summaries.jsonl` | 会话摘要 |
| `episodic/facts.jsonl` | 关键事实 |

### 查看聊天历史

`data/chat_history/congress.jsonl`

### Docker 容器调试

```bash
# 查看运行中的 Agent 容器
docker ps

# 进入容器
docker exec -it tlon-agent-{agent_id} bash
```

---

## 常见问题

| 问题 | 排查方法 |
|------|----------|
| Agent 不响应 | 检查 `respond_to_all` 配置和 @mention |
| 记忆未加载 | 检查 `data/agents/{id}/memory/` 目录 |
| Tool 执行失败 | 查看 Docker 容器日志 |

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [technical.md](./technical.md) | 技术栈 | L1 |

---

## 验证清单

- [x] 数据目录结构正确
- [x] 日志级别配置有效
