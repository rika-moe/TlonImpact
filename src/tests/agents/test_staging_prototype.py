"""Staging-area write gate (stage 4a trust gate).

``write_staging_prototype`` validates before anything touches disk: EDN
parse + ``MemberSpec.from_edn`` + the single ``validate_member_spec``
validator (host-less registry — Adam's host predicates must not pass).
"""

import pytest

from tlon.agents.prototypes import PrototypeError, write_staging_prototype
from tlon.base.paths import TlonPaths


class TestWriteStagingValidation:
    def test_unknown_action_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="unknown action"):
            write_staging_prototype(
                "wild",
                """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
""",
                tlon_paths=p,
            )
        # Nothing reached disk.
        assert not (p.prototypes_staging_dir() / "wild").exists()
