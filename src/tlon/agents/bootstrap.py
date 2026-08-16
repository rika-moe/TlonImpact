"""Standard-team bootstrap — first-boot initialization (wizard removal).

When the server starts on an **empty society** (no ``agent.yaml``
anywhere under ``~/.tlon/``), it instantiates the six standard-team
members from their role prototypes and writes their persona files
(creation-time data, ADR 0006). Idempotent: any existing agent skips the
whole bootstrap. This replaces the ``tlon mirror`` quickstart path so
installation reduces to ``uv sync && tlon dream && tlon ops``.

Called by ``ServerApp.initialize`` right after ``seed_prototypes`` and
before ``ConfigService`` reads the agent configs — the written files are
picked up by the normal config load path, so the standard team becomes a
society like any other.
"""

from tlon.agents.prototypes import instantiate, seed_prototypes
from tlon.base.config import yaml_load_all, yaml_save
from tlon.base.default_team import STANDARD_TEAM_DICTS
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

logger = get_logger(__name__)

#: role-card persona field -> member config filename (creation-time data)
_PERSONA_FIELDS: tuple[tuple[str, str], ...] = (
    ("identity", "IDENTITY.md"),
    ("profile", "SOUL.md"),
    ("behavior", "RULES.md"),
)


def bootstrap_standard_team(tlon_paths: TlonPaths | None = None) -> list[str]:
    """Instantiate the standard team on an empty society (idempotent).

    Seeds the prototype library (idempotent) and, when no agent config
    exists yet, instantiates each standard-team member from its role
    prototype (behavior genome) and writes the persona files (role-card
    data). Any existing agent skips the entire bootstrap.

    Args:
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        The created agent ids (``[]`` when the society already has members).
    """
    p = tlon_paths or paths
    seed_prototypes(p)

    existing = yaml_load_all(p.tlon_home())
    if existing:
        logger.info(
            "bootstrap_skipped", reason="agents_exist", existing_count=len(existing)
        )
        return []

    created: list[str] = []
    for role in STANDARD_TEAM_DICTS:
        cfg = instantiate(
            role["prototype"], role["id"], role["name"], tlon_paths=p
        )
        # Model identity is NOT snapshotted into agent.yaml: _build_llm_config
        # reads model_name/temperature from settings (env/.env) at runtime,
        # so a later Settings-view edit takes effect without touching the
        # members' configs. max_tokens/top_p stay at their defaults.
        yaml_save(cfg, p.tlon_home())

        config_dir = p.agent_config_dir(role["id"])
        for field, filename in _PERSONA_FIELDS:
            content = role.get(field, "")
            if content:
                (config_dir / filename).write_text(content.strip() + "\n", encoding="utf-8")

        created.append(cfg.id)
        logger.info(
            "bootstrap_agent_created",
            agent_id=cfg.id,
            agent_name=role["name"],
            prototype=role["prototype"],
        )

    logger.info(
        "bootstrap_standard_team_ready", count=len(created), agent_ids=created
    )
    return created
