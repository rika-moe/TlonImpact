"""Relay — a forwarding program carried by a prototype.

relay's ``script.tlon`` became a rules-form ``behavior.tlon`` with a
write-side ``:parameters`` template. Pins: the derived ``runtime == "script"``
(no loop rule), instantiation rendering the ``${to}`` placeholder into a
concrete ``(forward ...)``, and the required-params gate.
"""

import pytest

from tests.prototype_helpers import builtin_id
from tlon.agents.member_spec import MemberSpec
from tlon.agents.prototypes.catalog import get_prototype_info, seed_prototypes
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError
from tlosure.edn import loads


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
        info = get_prototype_info(builtin_id("定址转发"), tlon_paths=p)
        assert info is not None
        assert not hasattr(info, "runtime")

    def test_instantiate_renders_forward(self, tmp_path):
        p = _paths(tmp_path)
        cfg = instantiate(
            builtin_id("定址转发"),
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
        assert len(spec.rules[0].actions) == 2

    def test_missing_params_refused(self, tmp_path):
        p = _paths(tmp_path)
        with pytest.raises(PrototypeError, match="params"):
            instantiate(
                builtin_id("定址转发"), "relay-1", "转发者", model="openai/gpt-5", tlon_paths=p
            )

    def test_extra_params_refused(self, tmp_path):
        p = _paths(tmp_path)
        with pytest.raises(PrototypeError, match="unknown parameters"):
            instantiate(
                builtin_id("定址转发"),
                "relay-1",
                "转发者",
                model="openai/gpt-5",
                params={"to": ["a@tlon.internal"], "nope": "x"},
                tlon_paths=p,
            )
