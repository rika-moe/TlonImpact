"""Control calls are exclusive orchestration boundaries, never nested operations."""

from collections.abc import Iterable
from typing import Any

from tlon.operations.schema import InputError, json_value
from tlosure.edn import Form, Symbol

CONTROL_NAMES = frozenset({"finish-turn", "sleep-until", "exit-agent"})


def validate_control_forms(forms: Iterable[Any], *, allow_direct: bool = True) -> None:
    actions = tuple(forms)

    def mentions(value: Any) -> bool:
        if isinstance(value, Symbol):
            return str(value) in CONTROL_NAMES
        if isinstance(value, (tuple, list)):
            return any(mentions(item) for item in value)
        if isinstance(value, dict):
            return any(mentions(key) or mentions(item) for key, item in value.items())
        return False

    if not any(mentions(action) for action in actions):
        return
    action = actions[0]
    if (
        not allow_direct
        or len(actions) != 1
        or not isinstance(action, Form)
        or len(action) != 2
        or not isinstance(action[0], Symbol)
        or str(action[0]) not in CONTROL_NAMES
        or type(action[1]) is not dict
    ):
        raise InputError("control operation must be the sole direct action with literal inputs")
    # Evaluated argument expressions could dispatch effects before control is
    # checked. Literal JSON inputs exclude that hole at the load boundary.
    json_value(action[1], edn_keys=True)
