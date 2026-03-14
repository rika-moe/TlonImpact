"""Agent implementation for Tlon.

The core intelligent entity that can receive messages, think,
execute tools, and respond.
"""

import asyncio
from enum import Enum
from typing import Any, Dict, List, Optional

from tlon.organization.messaging import Congress
from tlon.base.events.event import Event, EventType
from tlon.organization.messaging import EventBus
from tlon.organization.messaging.message import Message
from tlon.base.logger import get_logger
from tlon.subsystem.skills import SkillManager
from tlon.subsystem.tools.skills.activate_skill import ActivateSkillTool
from tlon.subsystem.tools.skills.deactivate_skill import DeactivateSkillTool
from tlon.subsystem.tools.shell.execute_bash import ExecuteBashTool
from tlon.subsystem.tools.memory.forget_memory import ForgetMemoryTool
from tlon.subsystem.tools.memory.generate_summary import GenerateSummaryTool
from tlon.subsystem.tools.skills.load_skill_resource import LoadSkillResourceTool
from tlon.subsystem.tools.memory.remember_fact import RememberFactTool
from tlon.subsystem.tools.memory.search_memories import SearchMemoriesTool
from tlon.subsystem.tools.complete_dialog.send_message import SendMessageTool
from tlon.subsystem.tools.complete_dialog.complete_dialog import CompleteDialogTool
from tlon.subsystem.tools.tasks.create_task import CreateTaskTool
from tlon.subsystem.tools.tasks.list_tasks import ListTasksTool
from tlon.subsystem.tools.tasks.start_task import StartTaskTool
from tlon.subsystem.tools.tasks.pause_task import PauseTaskTool
from tlon.subsystem.tools.tasks.complete_task import CompleteTaskTool
from tlon.subsystem.tools.tasks.remove_task import RemoveTaskTool
from tlon.subsystem.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.base.config import AgentConfig
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.agents.llm_client import LLMClient, LLMResponse, ToolCall
from tlon.agents.tool_executor import ToolExecutor
from tlon.subsystem.memory import MemoryManager, MemoryConfig
from tlon.base.config import PromptTemplateManager, ConfigLoader
from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.subsystem.tools.tasks import TaskManager

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent lifecycle states."""
    STANDBY = "standby"      # Registered but not started
    INITIALIZING = "initializing"  # Starting up
    ACTIVE = "active"        # Running and processing messages
    THINKING = "thinking"    # Currently processing a message
    ERROR = "error"          # Encountered an error
    SHUTDOWN = "shutdown"    # Stopped


class Agent:
    """An intelligent agent that can process messages and execute tools.
    
    Each agent has:
    - A unique identity and role
    - An isolated Docker runtime environment
    - Long-term memory
    - Access to tools (bash execution, messaging, etc.)
    
    Example:
        >>> config = AgentConfig(id="dev-001", name="林知遥", ...)
        >>> agent = Agent(config, event_bus, chatroom, position_registry)
        >>> await agent.initialize()
        >>> # Agent will now respond to messages via EventBus
    """
    
    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,
        chatroom: Congress,
        position_registry = None,
    ):
        """Initialize the agent.
        
        Args:
            config: Agent configuration
            event_bus: Event bus for receiving messages
            chatroom: Chat room for sending messages
            position_registry: Position registry for looking up position info
        """
        self.config = config
        self.event_bus = event_bus
        self.chatroom = chatroom
        self.position_registry = position_registry
        
        self.id = config.id
        self.name = config.name
        self.personality = config.personality
        self.position_id = config.position_id
        
        # Initialize components
        # Merge YAML config with runtime settings for LLM
        from tlon.base.settings import settings
        llm_config = self._merge_llm_config(config.llm, settings)
        self.llm_client = LLMClient(llm_config)
        self.docker_runtime = DockerRuntime(config.docker, config.id)
        self.tool_executor = ToolExecutor()
        
        # Initialize skill manager
        self.skill_manager = SkillManager()
        self._skills_discovered = False
        
        # Initialize memory manager
        data_dir = chatroom.metadata.data_dir.parent
        memory_config = config.memory if isinstance(config.memory, MemoryConfig) else MemoryConfig()
        memory_config.agent_id = config.id
        # Use merged LLM config for summarization (needs API key)
        llm_config = self._merge_llm_config(config.llm, settings)
        self.memory = MemoryManager(
            agent_id=config.id,
            config=memory_config,
            data_dir=data_dir,
            llm_config=llm_config,
        )
        
        # Initialize task manager
        self.task_manager = TaskManager(
            agent_id=config.id,
            data_dir=str(data_dir),
        )
        
        # Subscription ID for event bus
        self._event_subscription: Optional[str] = None
        
        # Prompt template manager for building system prompts
        self._prompt_manager = PromptTemplateManager(ConfigLoader())
        
        # Agent state
        self.state = AgentState.STANDBY
        
        # Message queue for serial processing (Phase 4)
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._worker_running = False
        
        logger.info(f"Agent {self.name} ({self.id}) initialized")
    
    def _merge_llm_config(self, file_config, settings) -> "LLMConfig":
        """Merge LLM config from YAML file with runtime settings.
        
        File config provides model parameters (model_name, temperature, etc.)
        Settings provide runtime parameters (base_url, auth_token, etc.)
        
        Args:
            file_config: LLMConfig from YAML file
            settings: Application settings from environment variables
            
        Returns:
            Merged LLMConfig with all fields populated
        """
        from tlon.base.config import LLMConfig
        
        # If file_config is None, create default
        if file_config is None:
            file_config = LLMConfig()
        
        # Use file config for model parameters, settings for runtime parameters
        return LLMConfig(
            model_name=file_config.model_name,
            temperature=file_config.temperature,
            max_tokens=file_config.max_tokens,
            top_p=file_config.top_p,
            base_url=settings.llm_base_url,
            auth_token=settings.llm_api_key,
            max_retries=settings.llm_max_retries,
            base_delay=settings.llm_base_delay,
        )
    
    async def initialize(self) -> None:
        """Initialize the agent.
        
        Starts Docker container, loads memory, discovers skills,
        registers tools, and subscribes to events.
        """
        self.state = AgentState.INITIALIZING
        logger.info(f"Initializing agent: {self.name}")
        
        # Start Docker container
        await self.docker_runtime.ensure_running()
        logger.info(f"Docker container ready for {self.name}")
        
        # Load memory
        await self.memory.load()
        
        # Load tasks
        self.task_manager.load()
        
        # Discover skills (if configured)
        await self._discover_skills()
        
        # Register tools
        self._register_tools()
        
        # Subscribe to events
        self._event_subscription = self.event_bus.subscribe(
            EventType.MESSAGE_NEW,
            self._on_message
        )
        
        # Start message processing worker
        self._worker_running = True
        self._worker_task = asyncio.create_task(self._message_worker())
        
        self.state = AgentState.ACTIVE
        logger.info(f"Agent {self.name} ready")
    
    async def shutdown(self) -> None:
        """Shutdown the agent.
        
        Unsubscribes from events and stops Docker container.
        """
        logger.info(f"Shutting down agent: {self.name}")
        
        # Stop message worker
        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        # Unsubscribe from events
        if self._event_subscription:
            self.event_bus.unsubscribe(self._event_subscription)
        
        # Stop Docker container (but don't remove - persist for next time)
        await self.docker_runtime.stop()
        
        self.state = AgentState.SHUTDOWN
        logger.info(f"Agent {self.name} shutdown complete")
    
    async def _discover_skills(self) -> None:
        """Discover skills configured for this agent.
        
        This is the Discovery phase of the Agent Skills lifecycle.
        Only metadata is loaded, not full SKILL.md content.
        """
        if self._skills_discovered:
            return
        
        if self.config.skills:
            logger.info(f"Discovering skills for {self.name}: {self.config.skills}")
            skills = self.skill_manager.discover_skills(self.config.skills)
            logger.info(f"Discovered {len(skills)} skills for {self.name}")
            for skill in skills:
                logger.info(f"  - {skill.name}: {skill.description[:50]}...")
        else:
            logger.info(f"No skills configured for {self.name}")
        
        self._skills_discovered = True
    
    def _register_tools(self) -> None:
        """Register available tools."""
        # Built-in tools
        self.tool_executor.register_tool(
            ExecuteBashTool(self.docker_runtime)
        )
        self.tool_executor.register_tool(
            SendMessageTool(self.chatroom, self.id, self.name)
        )
        
        # Task completion tool (always last in the list to emphasize it's the exit)
        self.tool_executor.register_tool(
            CompleteDialogTool()
        )
        
        # Memory tools
        self.tool_executor.register_tool(
            RememberFactTool(self.memory)
        )
        self.tool_executor.register_tool(
            SearchMemoriesTool(self.memory)
        )
        self.tool_executor.register_tool(
            GenerateSummaryTool(self.memory)
        )
        self.tool_executor.register_tool(
            ForgetMemoryTool(self.memory)
        )
        logger.debug(f"Registered memory tools for {self.name}")
        
        # Skill tools (only if skills are configured)
        if self.config.skills:
            self.tool_executor.register_tool(
                ActivateSkillTool(self.skill_manager)
            )
            self.tool_executor.register_tool(
                LoadSkillResourceTool(self.skill_manager)
            )
            self.tool_executor.register_tool(
                DeactivateSkillTool(self.skill_manager)
            )
            logger.debug(f"Registered skill tools for {self.name}")
        
        # Task management tools
        self.tool_executor.register_tool(
            CreateTaskTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            ListTasksTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            StartTaskTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            PauseTaskTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            CompleteTaskTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            RemoveTaskTool(self.task_manager)
        )
        self.tool_executor.register_tool(
            GetCurrentTaskTool(self.task_manager)
        )
        logger.debug(f"Registered task management tools for {self.name}")
        
        logger.debug(f"Registered {len(self.tool_executor.list_tools())} tools for {self.name}")
    
    async def _on_message(self, event: Event) -> None:
        """Handle incoming message event.
        
        Phase 4: Messages are queued for serial processing instead of
        being processed immediately. This ensures that an agent processes
        messages one at a time, maintaining order.
        
        Args:
            event: The message event
        """
        message: Message = event.payload
        
        # Ignore own messages
        if message.sender_id == self.id:
            return
        
        # Check if we should respond
        if not self._should_respond(message):
            return
        
        # Queue the message for serial processing
        await self._message_queue.put(message)
        logger.debug(f"{self.name} queued message from {message.sender_name}")
    
    async def _message_worker(self) -> None:
        """Worker task to process messages serially from the queue.
        
        This ensures that an agent processes messages one at a time,
        maintaining order and preventing concurrent processing.
        """
        logger.info(f"{self.name} message worker started")
        
        while self._worker_running:
            try:
                # Wait for a message
                message = await self._message_queue.get()
                
                logger.info(f"{self.name} processing message from {message.sender_name}")
                
                # Set thinking state
                self.state = AgentState.THINKING
                
                # Process the message
                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    # Try to send an error response
                    try:
                        await self._send_message(
                            f"抱歉，处理消息时出现了错误: {str(e)[:100]}",
                            mentions=[message.sender_id] if message.sender_type == "human" else []
                        )
                    except Exception:
                        pass
                finally:
                    # Mark task as done and restore state
                    self._message_queue.task_done()
                    if self.state == AgentState.THINKING:
                        self.state = AgentState.ACTIVE
                    
            except asyncio.CancelledError:
                logger.info(f"{self.name} message worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in message worker: {e}", exc_info=True)
        
        logger.info(f"{self.name} message worker stopped")
    
    def _should_respond(self, message: Message) -> bool:
        """Determine if we should respond to this message.
        
        Phase 4 Multi-Agent Response Rules:
        - Proactive agents (proactive=True): Responds to direct user input + when @mentioned
        - Other agents (proactive=False): Only respond when @mentioned
        
        Args:
            message: The incoming message
            
        Returns:
            True if should respond
        """
        # Ignore own messages
        if message.sender_id == self.id:
            return False
        
        # Rule 1: Check if directly mentioned (in mentions list or content)
        if self._is_mentioned(message):
            logger.debug(f"{self.name} will respond: mentioned by {message.sender_name}")
            return True
        
        # Rule 2: Proactive agents respond to direct human input (no mentions)
        logger.debug(f"{self.name} checking proactive: proactive={self.config.proactive}, "
                    f"sender_type={message.sender_type}, mentions={message.mentions}")
        
        if self.config.proactive and message.sender_type == "human":
            # Check if message has no mentions (direct user input)
            if not message.mentions:
                logger.info(f"{self.name} will respond: proactive response to {message.sender_name}")
                return True
        
        # Not applicable - don't respond
        logger.debug(f"{self.name} will not respond to message from {message.sender_name}")
        return False
    
    def _is_mentioned(self, message: Message) -> bool:
        """Check if this agent is mentioned in the message.
        
        Only checks the message.mentions list (set by tools like completeDialog).
        Does NOT parse content for @mentions.
        
        - "林知遥" matches mention "林知遥" (exact)
        - "dev-001" matches mention "dev-001" (exact)
        
        Args:
            message: The message to check
            
        Returns:
            True if this agent is mentioned
        """
        # Check exact ID match in mentions list
        if self.id in message.mentions:
            return True
        
        # Check exact name match in mentions list
        if self.name in message.mentions:
            return True
        
        return False
    
    async def _process_message(self, message: Message) -> None:
        """Process a message and generate response.
        
        The main processing loop:
        1. Check idle timeout (generate summary if needed)
        2. Build context from history
        3. Call LLM to think
        4. Execute any tool calls
        5. Send final response
        
        Args:
            message: The message to process
        """
        logger.info(f"{self.name} is thinking...")
        
        # Check idle timeout before processing
        await self.memory.check_idle_timeout()
        
        # Build context
        context = await self._build_context(message)
        logger.debug(f"Context built with {len(context)} messages")
        
        # Track tool results for memory
        all_tool_results = []
        
        # Thinking loop (with tool execution)
        max_iterations = 10
        final_response = None
        final_mentions = []
        
        for iteration in range(max_iterations):
            logger.debug(f"Thinking iteration {iteration + 1}")
            
            # Call LLM
            try:
                response = await self.think(context)
                logger.debug(f"LLM response: finish_reason={response.finish_reason}, "
                            f"has_content={response.content is not None}, "
                            f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}")
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                error_msg = str(e)
                if "API key" in error_msg or "auth" in error_msg.lower():
                    error_msg = "API 密钥未配置或无效，请检查 .env 文件中的 OPENAI_API_KEY"
                elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                    error_msg = "请求超时，请检查网络连接或稍后重试"
                elif "connection" in error_msg.lower():
                    error_msg = "网络连接失败，请检查网络设置"
                
                await self._send_message(
                    f"抱歉，我无法连接到 AI 服务: {error_msg}",
                    mentions=[message.sender_id] if message.sender_type == "human" else []
                )
                return
            
            # Handle tool calls
            if response.tool_calls:
                tool_names = [tc.function_name for tc in response.tool_calls]
                logger.info(f"{self.name} is using {len(response.tool_calls)} tool(s): {', '.join(tool_names)}")
                
                # Check if completeTask is called
                complete_dialog_call = None
                other_tool_calls = []
                
                for tool_call in response.tool_calls:
                    if tool_call.function_name == "completeDialog":
                        complete_dialog_call = tool_call
                    else:
                        other_tool_calls.append(tool_call)
                
                # Execute all tools and collect results
                tool_results_list = []
                for tool_call in response.tool_calls:
                    # Log tool call with truncated arguments for readability
                    args_str = str(tool_call.arguments) if hasattr(tool_call, 'arguments') else "{}"
                    if len(args_str) > 100:
                        args_str = args_str[:100] + "..."
                    logger.info(f"  → Executing: {tool_call.function_name}({args_str})")
                    
                    tool_result = await self._execute_tool_call(tool_call)
                    
                    # Log result
                    success = tool_result.success if hasattr(tool_result, 'success') else False
                    msg = tool_result.message if hasattr(tool_result, 'message') and tool_result.message else ("OK" if success else "Failed")
                    logger.info(f"  ✓ Result: {msg}")
                    all_tool_results.append({
                        "tool_call": tool_call,
                        "result": tool_result,
                    })
                    tool_results_list.append((tool_call, tool_result))
                    
                    # If this is completeTask, extract the final response
                    if tool_call.function_name == "completeDialog" and tool_result.success:
                        final_response = tool_result.data.get("content", "")
                        final_mentions = tool_result.data.get("mentions", [])
                
                # Add assistant message with all tool calls (once)
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [self._tool_call_to_dict(tc) for tc in response.tool_calls]
                }
                if response.reasoning_content:
                    assistant_msg["reasoning_content"] = response.reasoning_content
                context.append(assistant_msg)
                
                # Add tool results
                for tool_call, tool_result in tool_results_list:
                    context.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": self._tool_result_to_string(tool_result),
                    })
                
                # If completeTask was called, send final response and exit
                if complete_dialog_call and final_response is not None:
                    logger.info(f"{self.name} is completing task with: {final_response[:50]}...")
                    try:
                        await self._send_message(final_response, mentions=final_mentions)
                        logger.info(f"{self.name} response sent successfully")
                    except Exception as e:
                        logger.error(f"Failed to send message: {e}", exc_info=True)
                    
                    # Save to memory
                    try:
                        await self.memory.add_interaction(
                            message, response, all_tool_results
                        )
                    except Exception as e:
                        logger.error(f"Failed to save to memory: {e}")
                    
                    return  # Exit the processing loop
                
                # Otherwise, continue to next iteration for more thinking
                continue
            
            # No tool calls - this is an error state as we require completeTask to finish
            logger.warning(f"{self.name} returned no tool calls. Waiting for completeTask...")
            
            # Add the assistant message to context to let it try again
            if response.content:
                context.append({
                    "role": "assistant",
                    "content": response.content,
                })
                context.append({
                    "role": "user",
                    "content": "请注意：你必须使用 completeDialog 工具来结束处理并发送最终回复。请不要直接输出文本，而是调用 completeDialog 工具。",
                })
            else:
                context.append({
                    "role": "user",
                    "content": "你必须使用 completeDialog 工具来完成对话。请调用 completeDialog(content=\"你的回复\") 来结束。",
                })
            
            continue
        else:
            # Max iterations reached
            logger.warning(f"Max iterations reached for {self.name}")
            await self._send_message(
                "思考时间过长，请简化您的问题。",
                mentions=[message.sender_id] if message.sender_type == "human" else []
            )
    
    async def think(self, context: List[Dict[str, Any]]) -> LLMResponse:
        """Call LLM to think and generate response.
        
        Args:
            context: Conversation context
            
        Returns:
            LLM response
        """
        system_prompt = await self._build_system_prompt()
        tools = self.tool_executor.get_tool_definitions()
        
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            messages=context,
            tools=tools if tools else None,
        )
        
        return response
    
    async def _build_context(self, current_message: Message) -> List[Dict[str, Any]]:
        """Build conversation context for LLM.
        
        Builds context from:
        1. ChatRoom history (all messages in the room)
        2. Agent's own memory (interactions it processed)
        
        Args:
            current_message: The current message to respond to
            
        Returns:
            List of messages in OpenAI format
        """
        context = []
        
        # Get recent messages from ChatRoom history
        # This ensures Agent has context even after restart
        room_history = await self.chatroom.get_history(limit=20)
        
        # Convert ChatRoom messages to OpenAI format
        for msg in room_history:
            # Skip system messages (join/leave notifications)
            if msg.sender_type == "system":
                continue
            
            # Skip the current message (will be added at the end)
            if msg.id == current_message.id:
                continue
            
            # Skip empty messages
            if not msg.content:
                continue
                
            if msg.sender_type == "agent":
                # Agent message - check if it's from this agent
                if msg.sender_id == self.id:
                    context.append({
                        "role": "assistant",
                        "content": msg.content,
                    })
                else:
                    # Message from other agents - treat as user context
                    context.append({
                        "role": "user",
                        "content": f"[{msg.sender_name}] {msg.content}",
                    })
            else:
                # Human message
                context.append({
                    "role": "user",
                    "content": msg.content,
                    "name": msg.sender_id,
                })
        
        # Add current message
        context.append({
            "role": "user",
            "content": current_message.content,
            "name": current_message.sender_id if current_message.sender_type != "agent" else None,
        })
        
        return context
    
    async def _build_system_prompt(self) -> str:
        """Build the system prompt for the agent.
        
        Uses SystemPromptBuilder to compose sections from:
        - Agent config (identity, personality)
        - Position config (role, rules)
        - Templates (workflow, important_notes, discipline_rules)
        - Runtime state (skills, memory, team)
        
        Returns:
            System prompt string
        """
        builder = SystemPromptBuilder(self)
        return await builder.build()
    
    def _build_role_prompt(self) -> str:
        """Build role-specific prompt from position configuration.
        
        Loads role description from position registry's jd field.
        """
        if self.position_registry and self.position_id:
            position = self.position_registry.get(self.position_id)
            if position and position.jd:
                return f"岗位职责:\n{position.jd}"
        
        return ""
    
    def _build_team_table(self) -> str:
        """Build a table of team members for the prompt."""
        participants = self.chatroom.get_all_participants()
        
        lines = []
        # 明确告诉 LLM 使用"名字"列来 @mention
        lines.append("| 名字（@mention时用这个名字） | 职务 | 职责 | 何时@他 |")
        lines.append("|---------------------------|------|------|---------|")
        
        for p in participants:
            if p.type == "human":
                lines.append(f"| {p.name} | 用户 | 提出需求和问题 | 任务完成时汇报 |")
            elif p.type == "agent":
                # Get role description from position registry
                if p.id == self.id:
                    continue  # Skip self
                
                role_info = self._get_role_info(p)
                lines.append(f"| {p.name} | {role_info['role']} | {role_info['desc']} | {role_info['when']} |")
        
        return "\n".join(lines) if len(lines) > 2 else ""
    
    def _get_role_info(self, participant) -> dict:
        """Get role info for a team member from position registry.
        
        Args:
            participant: The participant to get role info for
            
        Returns:
            Dictionary with role, desc, and when fields
        """
        # Get position_id from participant metadata
        position_id = participant.metadata.get("position_id", "") if participant.metadata else ""
        
        # Try to get position info from registry
        if self.position_registry and position_id:
            position = self.position_registry.get(position_id)
            if position:
                # Extract a brief description from jd (first line or first 20 chars)
                jd_summary = position.jd.strip().split("\n")[0] if position.jd else ""
                if len(jd_summary) > 30:
                    jd_summary = jd_summary[:30] + "..."
                
                return {
                    "role": position.title or position_id,
                    "desc": jd_summary or "协助处理问题",
                    "when": position.contact_when or "需要协助时",
                }
        
        # Fallback: return generic team member info
        return {"role": "团队成员", "desc": "协助处理问题", "when": "需要协助时"}
    
    def _get_agent_name_by_position(self, position_id: str) -> Optional[str]:
        """Get agent name by position ID from chatroom participants.
        
        Args:
            position_id: The position ID to look up
            
        Returns:
            Agent name if found, None otherwise
        """
        for p in self.chatroom.get_all_participants():
            if p.type == "agent" and p.metadata:
                if p.metadata.get("position_id") == position_id:
                    return p.name
        return None
    
    def _build_discipline_rules(self) -> str:
        """Build self-discipline rules for multi-agent collaboration."""
        # Get agent names for specific positions
        customer_service_name = self._get_agent_name_by_position("customer_service") or "客服"
        project_manager_name = self._get_agent_name_by_position("project_manager") or "项目经理"
        
        rules = [
            "1. **收到任务时**: 确认收到即可，不要重复 @发送者",
            f'   - ✅ 正确: "收到，我来处理"',
            f'   - ❌ 错误: "@{customer_service_name} 收到，我来处理"（不要回@）',
            "",
            "2. **任务完成时**: 向任务分派者汇报",
            f"   - 如果 @{project_manager_name} 派的活 → 完成后 @{project_manager_name}",
            "   - 如果 @人类 直接派的活 → 完成后 @人类",
            "",
            '3. **避免无意义回应**: 不要对 "收到"、"好的"、"谢谢"进行回复',
            "",
            "4. **循环检测**: 如果你和另一个 Agent 来回@超过2轮，停止回复，让人类介入",
            "",
            "5. **@mention 规则**: 使用 completeDialog 的 mentions 参数指定负责人",
            "   - 只有被 mentions 的人才会收到通知并响应",
            "   - 分派任务时必须通过 mentions 明确指定负责人",
        ]
        
        # Add position-specific rules from position configuration (excluding common rules)
        common_rules = {"使用 completeDialog 的 mentions 参数指定负责人", 
                       "分派任务时使用 completeDialog 的 mentions 参数指定负责人"}
        if self.position_registry and self.position_id:
            position = self.position_registry.get(self.position_id)
            if position and position.rules:
                # Filter out common rules that are already covered
                specific_rules = [r for r in position.rules if r not in common_rules]
                if specific_rules:
                    rules.extend([
                        "",
                        f"6. **{position.title}特殊规则**:",
                    ])
                    for rule in specific_rules:
                        rules.append(f"   - {rule}")
        
        return "\n".join(rules)
    
    async def _execute_tool_call(self, tool_call: ToolCall) -> Any:
        """Execute a tool call.
        
        Args:
            tool_call: The tool call to execute
            
        Returns:
            Tool execution result
        """
        logger.debug(f"Executing tool: {tool_call.function_name}")
        
        result = await self.tool_executor.execute(
            tool_call.function_name,
            tool_call.arguments
        )
        
        return result
    
    async def _send_message(self, content: str, mentions: Optional[List[str]] = None) -> None:
        """Send a message via the sendMessage tool.
        
        Args:
            content: Message content
            mentions: Optional list of user IDs to mention
        """
        try:
            tool = self.tool_executor.get_tool("sendMessage")
            result = await tool.execute(content=content, mentions=mentions or [])
            if not result.success:
                logger.error(f"sendMessage tool failed: {result.error}")
        except Exception as e:
            logger.error(f"Error in _send_message: {e}", exc_info=True)
            raise
    
    def _tool_call_to_dict(self, tool_call: ToolCall) -> Dict[str, Any]:
        """Convert ToolCall to dict for context."""
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function_name,
                "arguments": __import__('json').dumps(tool_call.arguments),
            }
        }
    
    def _tool_result_to_string(self, result: Any) -> str:
        """Convert tool result to string for context."""
        if hasattr(result, 'to_dict'):
            return __import__('json').dumps(result.to_dict())
        return str(result)
    
    def _extract_mentions(self, content: str) -> List[str]:
        """Extract @mentions from content."""
        import re
        pattern = r'@"([^"]+)"|@(\S+)'
        matches = re.findall(pattern, content)
        mentions = []
        for match in matches:
            mentions.extend([m for m in match if m])
        return mentions
