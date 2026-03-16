# 里程碑 4: 多 Agent 系统 (Congress)

**状态**: ✅ 已完成

## 目标
实现完整的多 Agent 团队，支持 Agent 间协作。

## 核心设计
- **响应规则**: 客服主动+被动，其他仅被动
- **启动策略**: 客服常驻，其他延迟启动
- **并发模型**: Agent 间并行，Agent 内串行

## 关键交付物
- AgentRegistry (生命周期管理)
- MessageRouter (路由决策)
- 消息队列（每个 Agent 独立 Queue）
- 团队表格注入 Prompt
- Congress (代表大会聊天室)

## 角色配置
- 客服 (proactive=true)
- 项目经理 (proactive=false)
- 开发工程师 (proactive=false)
- 运维工程师 (proactive=false)
- 产品经理 (proactive=false)
