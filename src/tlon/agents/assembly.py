"""Cognitive capability assembly for members (split from agent_registry, ).

``assemble_cognitive`` builds the full capability set one member gets —
LLM client, memory, task manager, Docker runtime, and the tool list —
and returns it as ``CognitiveCapabilities`` for ``MemberRuntime``. It is
a pure function of its parameters: the registry injects itself (it
satisfies the ``AgentLifecycle`` port structurally) plus the config
provider and paths, keeping the import direction agents → tools (the
ports live in ``tlon.tools.agents.ports``, ).
"""

from typing import TYPE_CHECKING

from tlon.agents.member_runtime import CognitiveCapabilities
from tlon.agents.prototypes import list_prototypes, write_staging_prototype
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, AgentConfig, LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.tools.agents.list_agents import ListAgentsTool
from tlon.tools.agents.spawn_agent import SpawnAgentTool
from tlon.tools.agents.stop_agent import StopAgentTool
from tlon.tools.agents.write_prototype import WritePrototypeTool
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.write_file import WriteFileTool
from tlon.tools.mail.send_email import SendEmailTool
from tlon.tools.mail.silence import SilenceTool
from tlon.tools.memory.forget_memory import ForgetMemoryTool
from tlon.tools.memory.remember_fact import RememberFactTool
from tlon.tools.memory.search_memories import SearchMemoriesTool
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool
from tlon.tools.shell.execute_bash import CheckBashTaskTool, ExecuteBashTool
from tlon.tools.tasks.complete_task import CompleteTaskTool
from tlon.tools.tasks.create_task import CreateTaskTool
from tlon.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.tools.tasks.list_tasks import ListTasksTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.pause_task import PauseTaskTool
from tlon.tools.tasks.remove_task import RemoveTaskTool
from tlon.tools.tasks.start_task import StartTaskTool

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


def _resolve_context_window(file_config: LLMConfig, model_name: str) -> int:
    """Resolve the model's context window (ADR 0012 phase 2).

    An explicit ``context_window`` in agent.yaml wins (fallback for
    missing/stale registry metadata); otherwise litellm's model registry
    (``get_model_info`` → ``max_input_tokens``); otherwise
    ``DEFAULT_CONTEXT_WINDOW``.
    """
    import litellm

    if file_config.context_window is not None:
        return file_config.context_window
    try:
        max_input_tokens = litellm.get_model_info(model_name).get("max_input_tokens")
        if max_input_tokens:
            return int(max_input_tokens)
    except Exception:
        pass
    logger.info(
        "context_window_fallback",
        model=model_name,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    return DEFAULT_CONTEXT_WINDOW


def _build_llm_config(file_config: LLMConfig | None, settings: Settings) -> LLMConfig:
    """Merge LLM config from YAML file with runtime settings.

    Model identity (``model_name`` / ``temperature``) is process-level
    config, driven by settings (env / ``{TLON_HOME}/.env``). The file's
    values are NOT used for these: bootstrap does not snapshot them into
    agent.yaml, so a Settings-view edit takes effect on restart. The file
    keeps the per-agent knobs the process does not manage: ``max_tokens`` /
    ``top_p`` / ``context_window`` (explicit override; None = auto-resolve
    from litellm's model registry).

    Settings provide the runtime parameters (base_url, auth_token, ...).
    The returned config always carries a resolved ``context_window`` int.
    """
    if file_config is None:
        file_config = LLMConfig()

    return LLMConfig(
        model_name=settings.llm_model_name,
        temperature=settings.llm_temperature,
        max_tokens=file_config.max_tokens,
        top_p=file_config.top_p,
        context_window=_resolve_context_window(file_config, settings.llm_model_name),
        base_url=settings.llm_base_url,
        auth_token=settings.llm_api_key,
        max_retries=settings.llm_max_retries,
        base_delay=settings.llm_base_delay,
    )


async def assemble_cognitive(
    config: AgentConfig,
    *,
    registry: "AgentRegistry",
    agent_info: "AgentInfoProvider",
    tlon_paths: TlonPaths,
) -> CognitiveCapabilities:
    """Assemble the capability set for a member.

    Unified member model (ADR 0006): every member gets the full
    capability set — LLM client, memory, task manager, Docker runtime,
    tools, and the team roster. Whether the cognitive engine (loop /
    scheduler) is actually attached is derived from the spec inside
    ``MemberRuntime``; a member without a loop rule simply never uses
    these objects (lazy assembly: objects are cheap, loading is on
    demand). The container is NOT started here: DockerRuntime lazily
    calls ensure_running() on first execute/read/write, so waking an
    agent for pure conversation costs zero containers — a daemon
    failure surfaces as a tool error, not a start failure.
    """
    from tlon.base.settings import get_settings

    # Build infrastructure dependencies
    llm_config = _build_llm_config(config.llm, get_settings())
    llm_client = LLMClient(llm_config)

    agent_home = tlon_paths.agent_dir(config.id)
    # Single memory tuning surface: `memory/config.json` per agent
    # (memory/models.py MemoryConfig). The agent_id is left empty here
    # so MemoryManager.load merges the on-disk config; there is no
    # agent.yaml memory section (the old base/config.MemoryConfig is
    # gone — its fields were never consumed).
    memory_config = MemoryConfig()
    memory = MemoryManager(
        agent_id=config.id,
        config=memory_config,
        data_dir=agent_home,
        llm_config=llm_config,
    )

    task_manager = TaskManager(
        agent_id=config.id,
        data_dir=str(agent_home),
    )

    # Create Docker runtime (shared by shell + filesystem tools).
    docker_runtime: DockerRuntime | None = None
    try:
        docker_runtime = DockerRuntime(config.docker, config.id)
    except Exception as e:
        logger.warning("docker_unavailable", agent_name=config.name, error=str(e))
    # No registry-side container handle is kept (ADR 0006): members
    # are never force-removed — a sleeping member keeps its container
    # (zero-cost wake); a re-assembly reuses it by name.

    # Create ToolExecutor (direct execution, no EventBus)
    tool_executor = ToolExecutor()

    # Register tools
    execute_bash_tool = ExecuteBashTool(config.docker, docker_runtime)
    tool_executor.register_tool(execute_bash_tool)
    # checkBashTask shares this agent's task table: no cross-agent visibility.
    tool_executor.register_tool(CheckBashTaskTool(execute_bash_tool.background_tasks))
    allowed_paths = config.tool.allowed_paths
    tool_executor.register_tool(ReadFileTool(config.docker, docker_runtime, allowed_paths))
    tool_executor.register_tool(WriteFileTool(config.docker, docker_runtime, allowed_paths))
    tool_executor.register_tool(EditFileTool(config.docker, docker_runtime, allowed_paths))
    tool_executor.register_tool(SendEmailTool())
    tool_executor.register_tool(SilenceTool())
    tool_executor.register_tool(ListPrototypesTool(lister=list_prototypes))
    # Spawn/stop lifecycle tools (stage 6b): the registry is the
    # assembler, so it injects itself (it satisfies the AgentLifecycle
    # port structurally); spawner_id binds the tool to the owning
    # agent for budget accounting and audit.
    tool_executor.register_tool(SpawnAgentTool(registry=registry, spawner_id=config.id))
    tool_executor.register_tool(StopAgentTool(registry=registry, spawner_id=config.id))
    tool_executor.register_tool(ListAgentsTool(registry=registry))
    # Spec authoring (stage 4a): writes untrusted prototypes to the
    # staging area; author_id is recorded as the :author for audit.
    tool_executor.register_tool(
        WritePrototypeTool(author_id=config.id, writer=write_staging_prototype)
    )
    tool_executor.register_tool(RememberFactTool(memory))
    tool_executor.register_tool(SearchMemoriesTool(memory))
    tool_executor.register_tool(ForgetMemoryTool(memory))
    tool_executor.register_tool(CreateTaskTool(task_manager))
    tool_executor.register_tool(ListTasksTool(task_manager))
    tool_executor.register_tool(StartTaskTool(task_manager))
    tool_executor.register_tool(PauseTaskTool(task_manager))
    tool_executor.register_tool(CompleteTaskTool(task_manager))
    tool_executor.register_tool(RemoveTaskTool(task_manager))
    tool_executor.register_tool(GetCurrentTaskTool(task_manager))
    logger.debug(
        "tools_registered",
        agent_name=config.name,
        tool_count=len(tool_executor.list_tools()),
    )

    # Initialize tools
    await tool_executor.initialize()

    # Prepare team members config (all other agents)
    all_ids = agent_info.get_all_ids()
    team_members = [
        cfg
        for aid in all_ids
        if aid != config.id and (cfg := agent_info.get_config(aid)) is not None
    ]

    # Read tool timeout from agent config
    per_tool_timeout = config.tool.per_timeout

    return CognitiveCapabilities(
        # Swap in the merged LLM config so consumers (context pressure,
        # member_runtime.py) see the resolved context_window int.
        config=config.model_copy(update={"llm": llm_config}),
        llm_client=llm_client,
        memory=memory,
        task_manager=task_manager,
        tool_executor=tool_executor,
        team_members=team_members,
        tlon_paths=tlon_paths,
        per_tool_timeout=per_tool_timeout,
        registry=registry,
    )
