"""First-boot seeding (wizard removal).

Seeds the built-in prototype library and skills into ``~/.tlon/``
(idempotent). Members are NEVER seeded (ADR 0016): the first boot is an
empty society plus the prototype library — the human creates members via
the WebUI / admin API, picking a model per member (there is no
process-level model fallback). Called by ``ServerApp.initialize`` before
``ConfigService`` reads the agent configs.
"""

from tlon.agents.prototypes import seed_prototypes
from tlon.agents.skills import seed_skills
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

logger = get_logger(__name__)


def bootstrap_society(tlon_paths: TlonPaths | None = None) -> None:
    """Seed the built-in prototypes and skills (idempotent).

    Args:
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon
    """
    p = tlon_paths or paths
    seeded = seed_prototypes(p)
    seeded_skills = seed_skills(p)
    logger.info("society_bootstrapped", prototypes=seeded, skills=seeded_skills)
