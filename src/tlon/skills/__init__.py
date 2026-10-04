"""Built-in skill package data (ADR 0010).

Each subdirectory is a skill: a ``SKILL.md`` (Agent Skills open standard,
see docs/design/skills.md) plus any ``scripts/`` / ``references/`` it
references. Seeded to the global layer ``~/.tlon/skills/`` on server
startup by :func:`tlon.agents.skills.seed_skills`; seeding never
overwrites existing files, so user edits and user-added skills survive.
"""
