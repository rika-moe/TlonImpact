"""Adam — the built-in init process of the agent world (PID 1).

Policy/mechanism split (docs/design/members.md, 阶段 D): the AgentRegistry
is pure mechanism (assembly, run table, locks, budget, idle-fact
detection); Adam is the policy. Since P5a the policy is *data*, not code:
Adam is the first configuration instance of the generic ScriptMember
runtime (``agents/script_member.py``) — a built-in ScriptSpec
(``agents/script_spec.py``, the ``ADAM_SPEC`` text below) plus registry
query closures injected into the script evaluator. All three lifecycle
policy lines live in the spec:

1. boot — after initialize(), every registered agent with
   ``activation != "lazy"`` that is not running gets an
   AGENT_START_REQUEST{reason="boot"}. The ``start-all`` action expands
   the candidate set in Python — the DSL never executes iteration,
   iteration is a builtin.
2. wake (socket activation) — MAIL_DELIVERED whose recipient is a
   sleeping agent gets AGENT_START_REQUEST{reason="wake"}. The mail is
   already durably in the agent's inbox, so no trigger event is carried:
   the woken agent scans its unread mail on boot (docs/design/mail.md).
3. drain — the registry's AGENT_IDLE fact event is turned into an
   AGENT_STOP_REQUEST{reason="drain"}.

Adam is a built-in Member (``member_id="adam"``, ``member_type="script"``):
it does not come from any user spec file, is not registered in the agent
registry, and does not appear in MemberDirectory. It holds no run-state
of its own — the injected query closures read the registry's read-only
interfaces (``get_agent`` / ``get_config`` / ``get_all``) instead of
reconstructing state from events. Lifecycle decisions carry no dedup
state either: the registry executes requests idempotently.

The dependency is one-directional: Adam holds a registry reference; the
registry never imports Adam (kernel first, PID 1 second — the DAG stays
acyclic).
"""

from typing import TYPE_CHECKING, Any

from tlon.agents.edn import loads
from tlon.agents.eval import make_script_builtins
from tlon.agents.script_member import ScriptMember
from tlon.agents.script_spec import ScriptSpec
from tlon.base.events.event_bus import EventBus

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

#: Adam's policy, as data. Parsed once per assembly by
#: :func:`make_adam`; behavior is exactly the pre-P5a hand-written Adam's.
ADAM_SPEC_EDN = """\
{:version 1
 :rules [{:name "boot"
          :on :boot
          :actions [(start-all {:reason "boot"})]}
         {:name "wake"
          :on :mail-delivered
          :when (sleeping? (get event :recipient))
          :actions [(start {:agent (get event :recipient) :reason "wake"})]}
         {:name "drain"
          :on :agent-idle
          :actions [(stop {:agent (get event :agent_id) :reason "drain"})]}]}
"""


class _AdamRegistryQueries:
    """Adam's host queries: the registry's read-only interfaces as closures.

    sleeping = registered (config known) but not running — the exact
    wake-gate semantics of the pre-P5a hand-written Adam. Non-string
    input (a missing payload key yields None) safely reads as False.
    """

    def __init__(self, registry: "AgentRegistry"):
        self._registry = registry

    def is_sleeping(self, agent_id: Any) -> bool:
        """Registered (config known) but not running."""
        if not isinstance(agent_id, str) or not agent_id:
            return False
        return (
            self._registry.get_agent(agent_id) is None
            and self._registry.get_config(agent_id) is not None
        )

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        return isinstance(agent_id, str) and self._registry.get_agent(agent_id) is not None

    def is_lazy(self, agent_id: Any) -> bool:
        """Registered with ``activation == "lazy"``."""
        if not isinstance(agent_id, str):
            return False
        config = self._registry.get_config(agent_id)
        return config is not None and config.activation == "lazy"

    def start_all_candidates(self) -> list[str]:
        """The boot set: registered, non-lazy, not already running."""
        candidates: list[str] = []
        for agent_id in self._registry.get_all():
            config = self._registry.get_config(agent_id)
            if config is None or config.activation == "lazy":
                continue
            if self._registry.get_agent(agent_id) is not None:
                continue
            candidates.append(agent_id)
        return candidates


def make_adam(event_bus: EventBus, agent_registry: "AgentRegistry") -> ScriptMember:
    """Assemble Adam: the built-in spec plus registry query closures.

    Args:
        event_bus: Event bus for subscriptions and request publishing
        agent_registry: The registry to query (read-only interfaces) and
            to address with start/stop requests
    """
    builtins = make_script_builtins(_AdamRegistryQueries(agent_registry))
    spec = ScriptSpec.from_edn(loads(ADAM_SPEC_EDN), builtins=builtins)
    return ScriptMember(member_id="adam", event_bus=event_bus, spec=spec, builtins=builtins)
