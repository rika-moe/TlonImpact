# 里程碑 1: CLI + 单 Agent

**状态**: ✅ 已完成

## 目标
验证核心架构，实现最小可用版本。

## 关键交付物
- Message/Event/EventBus 基础架构
- LLMClient 和 DockerRuntime
- Tool 系统（ExecuteBashTool）
- Agent 主类实现
- CLI 交互界面

## 验收标准
- [x] 可以运行: `python -m tlon`
- [x] 在 CLI 中输入消息，Agent 响应
- [x] 可以执行 bash 命令
- [x] 消息历史保存到 data/ 目录

## 技术要点
- 异步架构 (asyncio)
- OpenAI 兼容 API
- Docker 容器隔离
