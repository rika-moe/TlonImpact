"""Seed built-in prototypes, skills, and the one-time initial society (ADR 0036)."""

from tlon.agents.initial_society import seed_initial_society
from tlon.agents.prototypes.catalog import seed_prototypes
from tlon.agents.skills import seed_skills
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

logger = get_logger(__name__)


def bootstrap_society(tlon_paths: TlonPaths) -> None:
    """Seed libraries and create initial members only for a fresh home.

    Args:
        tlon_paths: path resolver for the target layout
    """
    seeded = seed_prototypes(tlon_paths)
    seeded_skills = seed_skills(tlon_paths)
    members = seed_initial_society(tlon_paths)
    logger.info("society_bootstrapped", prototypes=seeded, skills=seeded_skills, members=members)
