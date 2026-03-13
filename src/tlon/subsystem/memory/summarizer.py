"""LLM-based summarization and fact extraction for Memory system."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from tlon.agents.llm_client import LLMClient, LLMConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class Summarizer:
    """Uses LLM to generate session summaries and extract facts.
    
    This class handles the intelligent processing of working memory
    interactions to create meaningful summaries and extract structured
    facts for long-term storage.
    
    Example:
        >>> summarizer = Summarizer(llm_config)
        >>> summary, facts = await summarizer.process_interactions(interactions)
    """
    
    def __init__(self, llm_config: LLMConfig):
        """Initialize summarizer.
        
        Args:
            llm_config: Configuration for LLM client
        """
        self.llm_client = LLMClient(llm_config)
    
    async def generate_summary(
        self,
        interactions: List[Dict[str, Any]],
        temperature: Optional[float] = None,
    ) -> Tuple[str, List[str], List[str]]:
        """Generate a natural language summary from interactions.
        
        Args:
            interactions: List of interaction dictionaries
            
        Returns:
            Tuple of (summary_text, key_decisions, action_items)
        """
        if not interactions:
            return "No interactions to summarize.", [], []
        
        # Format interactions for the prompt
        formatted_interactions = self._format_interactions(interactions)
        
        system_prompt = """你是一个会话摘要生成助手。你的任务是分析用户与AI助手之间的对话历史，生成简洁但有信息量的中文摘要。

请生成一个自然语言摘要，总结这段会话的主要内容。摘要应该：
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
        
        messages = [
            {"role": "user", "content": f"请为以下对话生成摘要：\n\n{formatted_interactions}"}
        ]
        
        try:
            # Use provided temperature or fall back to config
            temp = temperature if temperature is not None else self.llm_client.config.temperature
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                temperature=temp,
                timeout=30.0,
            )
            
            if not response.content:
                logger.warning("LLM returned empty summary")
                return self._generate_fallback_summary(interactions), [], []
            
            # Parse JSON response
            try:
                result = self._extract_json(response.content)
                summary = result.get("summary", "").strip()
                key_decisions = result.get("key_decisions", [])
                action_items = result.get("action_items", [])
                
                # Validate and clean
                if not summary:
                    summary = self._generate_fallback_summary(interactions)
                
                # Ensure lists
                if not isinstance(key_decisions, list):
                    key_decisions = []
                if not isinstance(action_items, list):
                    action_items = []
                
                return summary, key_decisions, action_items
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse summary JSON: {e}")
                # Use the raw content as summary
                return response.content.strip(), [], []
                
        except Exception as e:
            logger.error(f"Error generating summary with LLM: {e}")
            return self._generate_fallback_summary(interactions), [], []
    
    async def extract_facts(
        self,
        interactions: List[Dict[str, Any]],
        existing_facts: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Extract key facts from interactions.
        
        Args:
            interactions: List of interaction dictionaries
            existing_facts: Optional list of existing facts to avoid duplicates
            
        Returns:
            List of fact dictionaries
        """
        if not interactions:
            return []
        
        formatted_interactions = self._format_interactions(interactions)
        
        # Build existing facts context
        existing_facts_text = ""
        if existing_facts:
            existing_facts_text = "已知事实（不要重复提取）：\n" + "\n".join([
                f"- {f.get('description', '')}" for f in existing_facts[-10:]
            ])
        
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
        
        user_content = "请从以下对话中提取事实：\n\n" + formatted_interactions
        if existing_facts_text:
            user_content += "\n\n" + existing_facts_text
        
        messages = [
            {"role": "user", "content": user_content}
        ]
        
        try:
            # Use provided temperature or fall back to config
            temp = temperature if temperature is not None else self.llm_client.config.temperature
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                temperature=temp,
                timeout=30.0,
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
                        validated_facts.append({
                            "fact_type": fact.get("fact_type", "GENERAL"),
                            "subject": fact.get("subject", "用户"),
                            "predicate": fact.get("predicate", "提到"),
                            "object": fact.get("object", ""),
                            "description": fact["description"],
                            "confidence": min(1.0, max(0.0, float(fact.get("confidence", 0.5)))),
                        })
                
                return validated_facts
                
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse facts JSON: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error extracting facts with LLM: {e}")
            return []
    
    async def process_interactions(
        self,
        interactions: List[Dict[str, Any]],
        session_id: str,
        existing_facts: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Process interactions to generate summary and extract facts.
        
        This is the main entry point that calls both generation methods.
        
        Args:
            interactions: List of interaction dictionaries
            session_id: Current session ID
            existing_facts: Optional existing facts to avoid duplicates
            temperature: Optional temperature override
            
        Returns:
            Tuple of (summary_dict, facts_list)
        """
        if not interactions:
            return None, []
        
        # Generate summary
        summary_text, key_decisions, action_items = await self.generate_summary(
            interactions, temperature=temperature
        )
        
        # Extract facts
        facts = await self.extract_facts(interactions, existing_facts, temperature=temperature)
        
        # Build summary dict
        summary_dict = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "summary_text": summary_text,
            "key_decisions": key_decisions,
            "action_items": action_items,
            "participants": self._extract_participants(interactions),
            "interaction_count": len(interactions),
        }
        
        return summary_dict, facts
    
    def _format_interactions(self, interactions: List[Dict[str, Any]]) -> str:
        """Format interactions for LLM prompt."""
        lines = []
        
        for i, inter in enumerate(interactions, 1):
            msg = inter.get("message", {})
            resp = inter.get("response", {})
            tool_results = inter.get("tool_results", [])
            
            # User message
            sender = msg.get("sender_name", "User")
            content = msg.get("content", "")
            lines.append(f"[{i}] {sender}: {content}")
            
            # Tool calls and results
            if tool_results:
                for tr in tool_results:
                    tool_name = tr.get("tool_name", "unknown")
                    stdout = tr.get("stdout", "")
                    if stdout:
                        lines.append(f"    [Tool: {tool_name}] {stdout[:150]}")
            
            # Agent response
            resp_content = ""
            if isinstance(resp, dict):
                resp_content = resp.get("content", "")
            elif isinstance(resp, str):
                resp_content = resp
            
            if resp_content:
                lines.append(f"    Agent: {resp_content[:200]}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def _extract_json(self, content: str) -> Dict[str, Any]:
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
        
        return json.loads(content)
    
    def _generate_fallback_summary(self, interactions: List[Dict[str, Any]]) -> str:
        """Generate a simple fallback summary without LLM."""
        if not interactions:
            return "No interactions."
        
        count = len(interactions)
        tool_count = sum(1 for i in interactions if i.get("tool_results"))
        
        # Get first and last message previews
        first_msg = interactions[0].get("message", {}).get("content", "")[:30]
        last_msg = interactions[-1].get("message", {}).get("content", "")[:30]
        
        return (
            f"会话包含 {count} 次交互，使用了 {tool_count} 次工具。"
            f"开始：\"{first_msg}...\"，结束：\"{last_msg}...\""
        )
    
    def _extract_participants(self, interactions: List[Dict[str, Any]]) -> List[str]:
        """Extract unique participant names from interactions."""
        participants = set()
        
        for inter in interactions:
            msg = inter.get("message", {})
            sender = msg.get("sender_name", "")
            if sender and sender != "Agent":
                participants.add(sender)
        
        return list(participants)
