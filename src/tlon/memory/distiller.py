"""LLM-based distillation of session projections into claims (ADR 0017)."""

import json
from typing import Any, cast

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

from .models import SessionProjection

logger = get_logger(__name__)


class Distiller:
    """Uses LLM to distill completed-session projections into claims.

    One call per batch: the projections (rebuilt from the session
    journals) go in, a list of natural-language claims (text +
    importance) comes out. Without an LLM (or on LLM failure) the caller
    falls back to mechanical claims — distillation must never lose a
    batch to provider flakiness.

    Example:
        >>> distiller = Distiller(llm_config)
        >>> claims = await distiller.distill(projections)
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        agent_id: str | None = None,
        tlon_paths: TlonPaths | None = None,
    ):
        """Initialize distiller.

        Args:
            llm_config: Configuration for LLM client
            agent_id: Owning member id for ledger attribution (issue
                ); None = no accounting
            tlon_paths: Path resolver for the ledger file; defaults to
                the global ``~/.tlon`` layout
        """
        self.llm_client = LLMClient(llm_config, agent_id=agent_id, tlon_paths=tlon_paths)

    async def distill(self, sessions: list[SessionProjection]) -> list[dict[str, Any]]:
        """Distill session projections into claims.

        Args:
            sessions: List of completed-session projections

        Returns:
            List of claim dictionaries with ``text`` and ``importance``;
            empty when the LLM yields nothing usable (caller falls back)
        """
        if not sessions:
            return []

        formatted_sessions = self._format_sessions(sessions)

        system_prompt = """你是一个记忆蒸馏助手。你的任务是分析用户与AI助手之间的对话历史，蒸馏出值得长期记住的断言（claim）。

每条断言应该：
1. 是一句独立的自然语言陈述（中文），不依赖上下文也能读懂
2. 记录明确提及的重要信息：用户偏好、项目状态、关键决策、重要发现
3. 不要推测，不要记录琐碎的寒暄

每条断言包含：
- text: 断言文本
- importance: 重要程度 (0.0-1.0)，日常琐事 0.3 左右，关键决策/偏好 0.7 以上

请用 JSON 格式返回：
{
    "claims": [
        {"text": "用户偏好使用 Python 进行数据分析", "importance": 0.7}
    ]
}"""

        messages = [
            {"role": "user", "content": f"请从以下会话中蒸馏记忆断言：\n\n{formatted_sessions}"}
        ]

        try:
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                timeout=30.0,
                purpose="distill",
            )

            if not response.content:
                logger.warning("distill_response_empty")
                return []

            try:
                result = self._extract_json(response.content)
                claims = result.get("claims", [])
                if not isinstance(claims, list):
                    return []

                validated = []
                for claim in claims:
                    if isinstance(claim, dict) and str(claim.get("text", "")).strip():
                        validated.append(
                            {
                                "text": str(claim["text"]).strip(),
                                "importance": min(
                                    1.0, max(0.0, float(claim.get("importance", 0.5)))
                                ),
                            }
                        )
                return validated

            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("distill_json_parse_failed", error=str(e))
                return []

        except Exception as e:
            logger.error("distill_failed", error=str(e), exc_info=True)
            return []

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
        content = content.strip()

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
    def fallback_claims(sessions: list[SessionProjection]) -> list[dict[str, Any]]:
        """Mechanical claims without LLM: quote the real trigger plus the
        session's first reply, one claim per session.

        The projection carries the session's real trigger content, so the
        fallback records actual user requests, never the agent's own
        reply mistaken for user input.
        """
        claims = []
        for session in sessions:
            trigger = session.trigger_content[:60]
            if not trigger:
                continue
            text = f"处理过请求：{trigger}"
            if session.llm_responses:
                text += f"；回复要点：{session.llm_responses[-1][:80]}"
            claims.append({"text": text, "importance": 0.3})
        return claims
