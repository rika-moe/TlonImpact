"""Read explicit run-loop request data from test programs."""

from tlon.agents.member_spec import MemberRule, SessionRecipe
from tlosure.edn import Form


def recipe_for(rule: MemberRule) -> SessionRecipe:
    """Return the first literal session recipe used by the fixture program."""
    for action in rule.actions:
        if isinstance(action, Form) and action and str(action[0]) == "run-loop":
            quoted = action[1]
            if isinstance(quoted, Form) and str(quoted[0]) == "quote":
                return SessionRecipe.from_edn(quoted[1])
    raise AssertionError(f"fixture rule {rule.name!r} has no literal run-loop request")
