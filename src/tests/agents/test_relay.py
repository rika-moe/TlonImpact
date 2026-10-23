"""Relay parameter declarations render into a copied ordinary module package."""

import pytest

from tests.prototype_helpers import builtin_id
from tlon.agents.program_package import read_module_sources, validate_program_sources
from tlon.agents.prototypes.catalog import get_prototype_info, seed_prototypes
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError


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
        modules = read_module_sources(p.agent_config_dir("relay-1") / "modules")
        source = modules["work/main.tlon"]
        assert "${to}" not in text + source
        assert ":parameters" not in text
        assert "a@tlon.internal" in source and "b@tlon.internal" in source
        spec = validate_program_sources(text, modules)
        assert dict(spec.handlers)["mail-delivered"] == "work.main/handle"
        assert "mail-forward" in source and "mail-mark-read" in source

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
