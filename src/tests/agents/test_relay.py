"""Relay — the migrated deterministic prototype (phase C file unification).

relay's ``script.tlon`` became a rules-form ``behavior.tlon`` with a
write-side ``:parameters`` template. Pins: the derived ``runtime == "script"``
(no loop rule), instantiation rendering the ``${to}`` placeholder into a
concrete ``(forward ...)``, and the required-params gate.
"""

import pytest

from tlon.agents.edn import loads
from tlon.agents.member_spec import MemberSpec
from tlon.agents.prototypes import (
    get_prototype_info,
    instantiate,
    seed_prototypes,
)
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError


def _paths(tmp_path) -> TlonPaths:
    p = TlonPaths(home=tmp_path)
    seed_prototypes(p)
    return p


class TestRelayPrototype:
    def test_prototype_declares_no_runtime(self, tmp_path):
        """Unified member model (ADR 0006): a prototype is a behavior
        template, not a member class — no runtime is declared or derived
        in its metadata."""
        p = _paths(tmp_path)
        info = get_prototype_info("relay", tlon_paths=p)
        assert info is not None
        assert not hasattr(info, "runtime")

    def test_instantiate_renders_forward(self, tmp_path):
        p = _paths(tmp_path)
        cfg = instantiate(
            "relay",
            "relay-1",
            "转发者",
            model="openai/gpt-5",
            params={"to": ["a@tlon.internal", "b@tlon.internal"]},
            tlon_paths=p,
        )
        assert cfg.id == "relay-1"

        text = p.agent_behavior_file("relay-1").read_text(encoding="utf-8")
        assert "${to}" not in text  # placeholder rendered away
        assert "a@tlon.internal" in text and "b@tlon.internal" in text

        spec = MemberSpec.from_edn(loads(text))
        assert spec.rules[0].name == "relay"
        assert spec.rules[0].loop is None
        assert len(spec.rules[0].actions) == 1

    def test_missing_params_refused(self, tmp_path):
        p = _paths(tmp_path)
        with pytest.raises(PrototypeError, match="params"):
            instantiate("relay", "relay-1", "转发者", model="openai/gpt-5", tlon_paths=p)

    def test_extra_params_refused(self, tmp_path):
        p = _paths(tmp_path)
        with pytest.raises(PrototypeError, match="unknown parameters"):
            instantiate(
                "relay",
                "relay-1",
                "转发者",
                model="openai/gpt-5",
                params={"to": ["a@tlon.internal"], "nope": "x"},
                tlon_paths=p,
            )
