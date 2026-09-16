"""LLM-based summarization and fact extraction for Memory system."""

import json
from datetime import datetime
from typing import Any, cast

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

from .models import SessionProjection

logger = get_logger(__name__)


class Summarizer:
    """Uses LLM to generate session summaries and extract facts.

    This class handles the intelligent processing of completed-session
    projections (rebuilt from the session journals, ADR 0009) to create
    meaningful summaries and extract structured facts for long-term
    storage.

    Example:
        >>> summarizer = Summarizer(llm_config)
        >>> summary, facts = await summarizer.process_sessions(projections)
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        agent_id: str | None = None,
        tlon_paths: TlonPaths | None = None,
    ):
        """Initialize summarizer.

        Args:
            llm_config: Configuration for LLM client
            agent_id: Owning member id for ledger attribution (issue
                ); None = no accounting
            tlon_paths: Path resolver for the ledger file; defaults to
                the global ``~/.tlon`` layout
        """
        self.llm_client = LLMClient(llm_config, agent_id=agent_id, tlon_paths=tlon_paths)

    async def generate_summary(
        self,
        sessions: list[SessionProjection],
        instructions: str | None = None,
    ) -> tuple[str, list[str], list[str]]:
        """Generate a natural language summary from session projections.

        Args:
            sessions: List of completed-session projections
            instructions: Optional focus for the summary, e.g. topics or
                decisions that must be preserved

        Returns:
            Tuple of (summary_text, key_decisions, action_items)
        """
        if not sessions:
            return "No sessions to summarize.", [], []

        # Format sessions for the prompt
        formatted_sessions = self._format_sessions(sessions)

        system_prompt = """你是一个会话摘要生成助手。你的任务是分析用户与AI助手之间的对话历史，生成简洁但有信息量的中文摘要。

请生成一个自然语言摘要，总结这些会话的主要内容。摘要应该：
1. 概括用户的主要请求和意图
2. 说明AI助手执行了哪些操作（如工具调用）
3. 提及任何重要的发现或结果
4. 控制在100-200字以内

同时，提取以下内容：
- key_decisions: 会话中做出的关键决策（如果有）
- action_items: 需要后续跟进的事项（如果有）

请用 JSON 格式返回：
{
    "summary": "摘要文本",
    "key_decisions": ["决策1", "决策2"],
    "action_items": ["待办1", "待办2"]
}"""

        if instructions:
            system_prompt += f"\n\n请特别保留/关注以下内容：{instructions}"

        messages = [
            {"role": "user", "content": f"请为以下会话生成摘要：\n\n{formatted_sessions}"}
        ]

        try:
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                timeout=30.0,
                purpose="summarize",
            )

            if not response.content:
                logger.warning("summary_response_empty")
                return self._generate_fallback_summary(sessions), [], []

            # Parse JSON response
            try:
                result = self._extract_json(response.content)
                summary = result.get("summary", "").strip()
                key_decisions = result.get("key_decisions", [])
                action_items = result.get("action_items", [])

                # Validate and clean
                if not summary:
                    summary = self._generate_fallback_summary(sessions)

                # Ensure lists
                if not isinstance(key_decisions, list):
                    key_decisions = []
                if not isinstance(action_items, list):
                    action_items = []

                return summary, key_decisions, action_items

            except json.JSONDecodeError as e:
                logger.warning("summary_json_parse_failed", error=str(e))
                # Use the raw content as summary
                return response.content.strip(), [], []

        except Exception as e:
            logger.error("summary_generation_failed", error=str(e), exc_info=True)
            return self._generate_fallback_summary(sessions), [], []

    async def extract_facts(
        self,
        sessions: list[SessionProjection],
        existing_facts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract key facts from session projections.

        Args:
            sessions: List of completed-session projections
            existing_facts: Optional list of existing facts to avoid duplicates

        Returns:
            List of fact dictionaries
        """
        if not sessions:
            return []

        formatted_sessions = self._format_sessions(sessions)

        # Build existing facts context
        existing_facts_text = ""
        if existing_facts:
            existing_facts_text = "已知事实（不要重复提取）：\n" + "\n".join(
                [f"- {f.get('description', '')}" for f in existing_facts[-10:]]
            )

        system_prompt = """你是一个信息提取助手。从用户与AI的对话中提取重要的事实和偏好。

提取的事实类型包括：
- USER_PREFERENCE: 用户的偏好、习惯、喜好
- PROJECT_STATE: 项目状态、配置、设置
- TECH_STACK: 技术栈、使用的工具或框架
- DECISION: 明确的决策或选择
- GENERAL: 其他重要信息

每个事实应包含：
- fact_type: 事实类型
- subject: 主体（如"用户"、"项目"）
- predicate: 谓词（如"使用"、"喜欢"）
- object: 对象
- description: 自然语言描述
- confidence: 置信度 (0.0-1.0)

只提取明确提及、比较重要的事实。不要推测。

请用 JSON 格式返回：
{
    "facts": [
        {
            "fact_type": "USER_PREFERENCE",
            "subject": "用户",
            "predicate": "喜欢",
            "object": "Python",
            "description": "用户表示喜欢使用Python编程",
            "confidence": 0.9
        }
    ]
}"""

        user_content = "请从以下会话中提取事实：\n\n" + formatted_sessions
        if existing_facts_text:
            user_content += "\n\n" + existing_facts_text

        messages = [{"role": "user", "content": user_content}]

        try:
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                timeout=30.0,
                purpose="extract_facts",
            )

            if not response.content:
                return []

            try:
                result = self._extract_json(response.content)
                facts = result.get("facts", [])

                # Validate facts
                validated_facts = []
                for fact in facts:
                    if isinstance(fact, dict) and fact.get("description"):
                        validated_facts.append(
                            {
                                "fact_type": fact.get("fact_type", "GENERAL"),
                                "subject": fact.get("subject", "用户"),
                                "predicate": fact.get("predicate", "提到"),
                                "object": fact.get("object", ""),
                                "description": fact["description"],
                                "confidence": min(
                                    1.0, max(0.0, float(fact.get("confidence", 0.5)))
                                ),
                            }
                        )

                return validated_facts

            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("facts_json_parse_failed", error=str(e))
                return []

        except Exception as e:
            logger.error("facts_extraction_failed", error=str(e), exc_info=True)
            return []

    async def process_sessions(
        self,
        sessions: list[SessionProjection],
        existing_facts: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Process session projections to generate a summary and extract facts.

        This is the main entry point that calls both generation methods.

        Args:
            sessions: Completed-session projections (the batch to summarize)
            existing_facts: Optional existing facts to avoid duplicates
            instructions: Optional focus for the summary, e.g. topics or
                decisions that must be preserved

        Returns:
            Tuple of (summary_dict, facts_list)
        """
        if not sessions:
            return None, []

        # Generate summary
        summary_text, key_decisions, action_items = await self.generate_summary(
            sessions, instructions=instructions
        )

        # Extract facts
        facts = await self.extract_facts(sessions, existing_facts)

        # Build summary dict
        summary_dict = {
            "timestamp": datetime.now().isoformat(),
            "summary_text": summary_text,
            "key_decisions": key_decisions,
            "action_items": action_items,
            "interaction_count": sum(len(s.llm_responses) for s in sessions),
        }

        return summary_dict, facts

    def _format_sessions(self, sessions: list[SessionProjection]) -> str:
        """Format session projections for LLM prompt."""
        lines = []

        for i, session in enumerate(sessions, 1):
            lines.append(f"[会话 {i}] 触发：{session.trigger_content}")

            for resp in session.llm_responses:
                lines.append(f"    Agent: {resp[:200]}")

            for tool_line in session.tool_results:
                lines.append(f"    [Tool] {tool_line[:150]}")

            lines.append("")

        return "\n".join(lines)

    def _extract_json(self, content: str) -> dict[str, Any]:
        """Extract JSON from LLM response."""
        # Try to find JSON block
        content = content.strip()

        # Check if wrapped in markdown code block
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()

        return cast(dict[str, Any], json.loads(content))

    @staticmethod
    def _generate_fallback_summary(sessions: list[SessionProjection]) -> str:
        """Generate a simple fallback summary without LLM.

        The projection carries the session's real trigger content, so the
        fallback quotes the actual user requests (the retired working
        memory only had the agent's own reply and quoted that instead).
        """
        if not sessions:
            return "No sessions."

        count = len(sessions)
        tool_count = sum(len(s.tool_results) for s in sessions)

        # Get first and last trigger previews
        first_trigger = sessions[0].trigger_content[:30]
        last_trigger = sessions[-1].trigger_content[:30]

        return (
            f"包含 {count} 个会话，共调用 {tool_count} 次工具。"
            f'首个请求："{first_trigger}..."，最后请求："{last_trigger}..."'
        )
