"""Agent lifecycle tools (stage 6b of docs/design/dsl-agents.md).

提供 agent 从 prototype 派生临时工（contractor）、停止临时工、
查看全部 agent 状态的能力。所有 spawn 受 registry 的预算约束
（全局 activation 上限 / spawn 深度 / 每 session 配额），权限不可升格：
只能从 prototype 库派生，overrides 仅覆盖 behavior 字段。
"""
