"""Admin authors and reviews complete module packages through the facade."""

import json
from pathlib import Path

from tests.server.test_rest_admin_api import StubRequest, _handler

BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle}}"
MODULES = {"work/reply.tlon": "(ns work.reply (:export handle)) (defn handle [event] event)"}


async def test_human_staging_full_package_approval(tmp_path: Path) -> None:
    handler, _, paths = _handler(tmp_path)
    response = await handler.handle_create_staging_prototype(
        StubRequest(body={"display_name": "Reply", "behavior": BEHAVIOR, "modules": MODULES})
    )
    assert response.status == 201
    name = json.loads(response.text)["prototype"]["name"]
    response = await handler.handle_get_staging_prototype(StubRequest(name=name))
    assert json.loads(response.text)["prototype"]["modules"] == MODULES
    assert not json.loads(response.text)["prototype"]["trusted"]
    response = await handler.handle_approve_prototype(StubRequest(name=name))
    assert response.status == 200
    assert not (paths.prototypes_staging_dir() / name).exists()
    response = await handler.handle_get_prototype(StubRequest(name=name))
    assert json.loads(response.text)["prototype"]["modules"] == MODULES
    replacement = {"work/reply.tlon": MODULES["work/reply.tlon"] + "\n"}
    response = await handler.handle_update_prototype(
        StubRequest(name=name, body={"modules": replacement})
    )
    assert response.status == 400
    assert "immutable" in json.loads(response.text)["error"]
    response = await handler.handle_get_prototype(StubRequest(name=name))
    assert json.loads(response.text)["prototype"]["modules"] == MODULES


async def test_invalid_module_payload_rejected(tmp_path: Path) -> None:
    handler, _, paths = _handler(tmp_path)
    before = set(paths.prototypes_dir().iterdir())
    for modules in (["not-a-map"], {"work/reply.tlon": 5}, {"../escape.tlon": "(ns escape)"}):
        response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "Reply", "behavior": BEHAVIOR, "modules": modules})
        )
        assert response.status == 400
    assert set(paths.prototypes_dir().iterdir()) == before
