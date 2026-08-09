"""Adam — the built-in init process of the agent world (PID 1).

Policy/mechanism split (docs/design/members.md, 阶段 D): the AgentRegistry
is pure mechanism (assembly, run table, locks, budget, idle-fact
detection); Adam is the policy. The policy is *data*, not code: Adam is
the first configuration instance of the unified MemberRuntime
(``agents/member_runtime.py``, phase C) — a built-in MemberSpec (the
``ADAM_SPEC`` text below) parsed directly with the unified loader
semantics and validated at load, plus registry query closures injected
into the evaluator. Two lifecycle policy lines live in the spec:

1. boot — after initialize(), every registered member whose spec
   declares ``:on :boot`` (phase E: spec-derived, see ``boot_candidates``)
   that is not running gets an AGENT_START_REQUEST{reason="boot"}. The
   ``start-all`` action expands the candidate set in Python — the DSL
   never executes iteration, iteration is a builtin.
2. drain — the registry's AGENT_IDLE fact event is turned into an
   AGENT_STOP_REQUEST{reason="drain"}.

Wake is no longer Adam's line: phase E moved wake-on-mail into the
registry as a mechanized policy — the registry derives wake eligibility
from each member's spec (``:on :mail-delivered``) and issues its own
AGENT_START_REQUEST{reason="wake"}, so the spec is the single lifecycle
authority for both the boot set and the wake gate.

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
from tlon.agents.eval import make_member_builtins
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
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
        """A member that can sleep / be drained (phase E: spec-derived)."""
        return self._registry.is_lazy(agent_id)

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot`` (phase E)."""
        return self._registry.boot_candidates()


def make_adam(event_bus: EventBus, agent_registry: "AgentRegistry") -> MemberRuntime:
    """Assemble Adam: the built-in spec plus registry query closures.

    Adam is a deterministic member (no cognitive rule → cognitive=None): it
    holds no registry reference beyond the injected query closures.

    Args:
        event_bus: Event bus for subscriptions and request publishing
        agent_registry: The registry to query (read-only interfaces) and
            to address with start/stop requests
    """
    builtins = make_member_builtins(_AdamRegistryQueries(agent_registry))
    spec = MemberSpec.from_edn(loads(ADAM_SPEC_EDN))
    # Single-validator gate with the host builtins: the boot rule's
    # ``start-all`` action needs the registry host for candidate expansion,
    # which the host-less deterministic branch would reject.
    spec = validate_member_spec(spec, builtins=builtins)
    return MemberRuntime(
        member_id="adam",
        name="adam",
        event_bus=event_bus,
        spec=spec,
        builtins=builtins,
        cognitive=None,
    )
