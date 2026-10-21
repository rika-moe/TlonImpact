"""Instantiation: create a member from a prototype (copy, not reference)."""

from typing import Any

from tlon.agents.behavior_template import (
    BehaviorTemplateError,
    analyze,
    render_template,
)
from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError
from tlon.agents.prototypes.info import BEHAVIOR_FILE, LESSONS_FILE, _is_prototype_dir
from tlon.agents.prototypes.staging import _validate_behavior
from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig, yaml_exists, yaml_save
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError
from tlosure.edn import EDNParseError, loads

logger = get_logger(__name__)


def instantiate(
    prototype_name: str,
    agent_id: str,
    agent_name: str,
    *,
    model: str,
    params: dict[str, Any] | None = None,
    tlon_paths: TlonPaths,
) -> AgentConfig:
    """Create an agent from a prototype: copy, no merging.

    Persona files are copied verbatim; the prototype ``behavior.tlon`` —
    a complete, total spec in the unified rules form — is copied verbatim
    as the instance's own behavior file.

    When the prototype's spec declares ``:parameters`` (a parameterized
    write-side template), ``params`` must provide concrete values; the
    file is rendered with those values before writing to disk. Otherwise
    ``params`` must be empty.

    ``agent.yaml`` is written via ``yaml_save`` (the standard config save
    path). The new agent takes effect without a restart: the caller
    (ops admin API / registry spawn) hot-adopts it — the caller
    requests ``POST /admin/config/agents/{id}/reload`` →
    ``AgentRegistry.adopt_agent``); when no server is running, it takes
    effect after the next server restart.

    Args:
        prototype_name: prototype directory name under ~/.tlon/prototypes/
        agent_id: new agent id (must not already exist)
        agent_name: display name of the new agent
        model: the member's LLM in ``provider/model`` form (ADR 0016 —
            model selection is deployment policy, not genotype; it lands
            in agent.yaml as ``llm.model_name`` and assembly hard-fails
            without it)
        params: template parameter values (required when the prototype
            declares ``:parameters``; must be empty otherwise)
        tlon_paths: path resolver for the target layout

    Returns:
        The saved AgentConfig (with ``prototype`` recording the source).

    Raises:
        PrototypeError: prototype missing, agent id already taken, model
            empty, or required params missing / invalid.
    """
    p = tlon_paths
    if not model or not model.strip():
        raise PrototypeError(
            "model is required (provider/model 形式，如 deepseek/deepseek-chat)——"
            "成员级 LLM 配置没有进程级兜底（ADR 0016）"
        )
    proto_dir = p.prototypes_dir() / prototype_name
    if not _is_prototype_dir(proto_dir):
        raise PrototypeError(f"prototype '{prototype_name}' not found under {p.prototypes_dir()}")
    if yaml_exists(agent_id, p.tlon_home()):
        raise PrototypeError(f"agent '{agent_id}' already exists")

    behavior_file = proto_dir / BEHAVIOR_FILE
    raw_text = behavior_file.read_text(encoding="utf-8")

    info = analyze(raw_text)
    if info.has_parameters:
        if params is None:
            raise PrototypeError(
                f"prototype '{prototype_name}' declares "
                f":parameters {sorted(info.placeholders)!r} — "
                f"params must be provided at instantiation"
            )
        try:
            behavior_text = render_template(raw_text, params)
        except BehaviorTemplateError as e:
            raise PrototypeError(f"prototype '{prototype_name}' parameter error: {e}") from e
    else:
        if params:
            raise PrototypeError(
                f"prototype '{prototype_name}' does not declare "
                f":parameters, but params were provided"
            )
        behavior_text = raw_text

    # Validate rendered content before creating member files.
    behavior_text = _validate_behavior(behavior_text, tlon_paths=p)

    # Behavior only — copy, not reference (frozen at creation time; a
    # spec is total, so there is nothing to merge). Unified member model
    # (ADR 0006): prototypes carry no persona — a member's persona files
    # are seeded at creation (wizard role cards) and member-owned
    # afterwards (self-editable via persona-update, ADR 0024), not part
    # of the behavior genome.
    config_dir = p.agent_config_dir(agent_id)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / BEHAVIOR_FILE).write_text(behavior_text, encoding="utf-8")

    # Lessons inheritance (see docs/design/prototypes.md): a prototype's
    # LESSONS.md — lessons distilled from a retired member — is copied
    # next to the behavior file
    # (a persona-file neighbor); the DSL ``(lessons)`` section injects it.
    # No LESSONS.md in the prototype → no file on the member either.
    prototype_lessons = proto_dir / LESSONS_FILE
    if prototype_lessons.exists():
        (config_dir / LESSONS_FILE).write_text(
            prototype_lessons.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # The rendered spec is parsed once here as a validation gate (the
    # loader's full validation still gates the member at boot). Model
    # selection is deployment policy, not genotype (ADR 0016): the spec
    # grammar rejects a top-level ``:llm`` key outright — the caller's
    # ``model`` argument is the only source, landing in agent.yaml as
    # ``llm.model_name``.
    try:
        spec = MemberSpec.from_edn(loads(behavior_text))
    except (EDNParseError, MemberSpecValidationError) as e:
        raise PrototypeError(f"prototype '{prototype_name}' behavior failed to parse: {e}") from e

    # Execution capability (constitution 1, docs/design/execution-device.md):
    # the spec's ``:execution`` declaration is genotype, reviewed with the
    # proposal; it is projected once here into agent.yaml's ``docker.level``.
    # The runtime authority stays agent.yaml — a member editing its own
    # behavior file afterwards does not re-project (no self-escalation).
    # Without the section the config default ("L1") applies unchanged.
    docker = DockerRuntimeConfig()
    if spec.execution_level is not None:
        docker = DockerRuntimeConfig(level=spec.execution_level)

    # agent.yaml via the same save path as the wizard
    agent = AgentConfig(
        id=agent_id,
        name=agent_name,
        prototype=prototype_name,
        llm=LLMConfig(model_name=model.strip()),
        docker=docker,
    )
    yaml_save(agent, p.tlon_home())
    logger.info(
        "prototype_instantiated",
        prototype=prototype_name,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    return agent
