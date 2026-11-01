"""Recoverable Gitea account and organization bootstrap."""

import base64
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tlon.gitea.control import StartupControl
from tlon.gitea.storage import GiteaError

ADMIN = "tlon-service"
OPERATOR = "tlon-operator"
ORGANIZATION = "tlon"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(
    url: str,
    path: str,
    password: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> tuple[int, Any]:
    authorization = base64.b64encode(f"{ADMIN}:{password}".encode()).decode()
    req = Request(
        url + "api/v1/" + path,
        method=method,
        headers={"Authorization": "Basic " + authorization, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=5) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        return exc.code, None


def health_ready(url: str) -> bool:
    """The unauthenticated health endpoint works before the first user exists."""
    req = Request(url + "api/healthz", method="GET")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=5) as response:
            return bool(
                response.status == 200 and json.loads(response.read()).get("status") == "pass"
            )
    except HTTPError:
        return False


def run_admin_command(container: Any, command: list[str], control: StartupControl) -> int:
    """Detached exec avoids the SDK's unbounded hijacked-socket reads."""
    control.check()
    execution = container.client.api.exec_create(
        container.id,
        ["/usr/bin/timeout", "-s", "KILL", "30", *command],
        stdout=False,
        stderr=False,
    )
    control.check()
    container.client.api.exec_start(execution["Id"], detach=True)
    deadline = time.monotonic() + 35
    while True:
        control.check()
        result = container.client.api.exec_inspect(execution["Id"])
        if not result["Running"]:
            return int(result["ExitCode"])
        if time.monotonic() >= deadline:
            raise GiteaError("Gitea administrator initialization timed out")
        control.pause(0.2)


def initialize(
    container: Any,
    url: str,
    credentials: dict[str, str],
    *,
    timeout: float = 60,
    control: StartupControl | None = None,
) -> None:
    control = control or StartupControl()
    control.check()
    deadline = time.monotonic() + timeout
    while True:
        control.check()
        try:
            if health_ready(url):
                break
        except (URLError, TimeoutError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise GiteaError("Gitea HTTP readiness timed out")
        control.pause(0.5)
    password = credentials["admin_password"]
    code, admin = request(url, "user", password)
    if code == 401:
        result = run_admin_command(
            container,
            [
                "gitea",
                "--config",
                "/etc/gitea/app.ini",
                "admin",
                "user",
                "create",
                "--username",
                ADMIN,
                "--password",
                password,
                "--email",
                "service@tlon.invalid",
                "--admin",
                "--must-change-password=false",
            ],
            control,
        )
        # A previous request may have succeeded before its response was lost.
        code, admin = request(url, "user", password)
        if result != 0 and code != 200:
            raise GiteaError("Gitea management account initialization failed")
    if code != 200 or not admin.get("is_admin") or admin.get("login") != ADMIN:
        raise GiteaError("Gitea management authentication failed; explicit recovery required")
    control.check()
    code, _ = request(url, f"users/{OPERATOR}", password)
    if code == 404:
        control.check()
        code, _ = request(
            url,
            "admin/users",
            password,
            method="POST",
            body={
                "username": OPERATOR,
                "password": credentials["operator_password"],
                "email": "operator@tlon.invalid",
                "must_change_password": True,
                "send_notify": False,
            },
        )
        if code != 201:
            raise GiteaError("Gitea human account initialization failed")
    elif code != 200:
        raise GiteaError("Gitea human account lookup failed")
    code, _ = request(url, f"orgs/{ORGANIZATION}", password)
    if code == 404:
        control.check()
        code, _ = request(
            url,
            f"admin/users/{OPERATOR}/orgs",
            password,
            method="POST",
            body={
                "username": ORGANIZATION,
                "full_name": "Tlon",
                "visibility": "private",
            },
        )
        if code != 201:
            raise GiteaError("Gitea organization initialization failed")
    elif code != 200:
        raise GiteaError("Gitea organization lookup failed")
    teams: list[dict[str, Any]] = []
    page = 1
    while True:
        control.check()
        code, batch = request(url, f"orgs/{ORGANIZATION}/teams?limit=50&page={page}", password)
        if code != 200 or not isinstance(batch, list):
            raise GiteaError("Gitea organization verification failed")
        teams.extend(batch)
        if len(batch) < 50:
            break
        page += 1
    owners = next((team for team in teams if team.get("name") == "Owners"), None)
    if not owners:
        raise GiteaError("Gitea organization owner team missing")
    code, _ = request(url, f"teams/{owners['id']}/members/{OPERATOR}", password)
    if code != 200:
        raise GiteaError("Gitea human organization ownership mismatch")
    member_team = next((team for team in teams if team.get("name") == "members"), None)
    if member_team is not None and (
        member_team.get("units_map")
        != {"repo.code": "read", "repo.issues": "read", "repo.pulls": "read"}
        or member_team.get("can_create_org_repo") is not False
        or member_team.get("includes_all_repositories") is not True
    ):
        raise GiteaError("Gitea member team permissions mismatch")
    if member_team is None:
        control.check()
        code, _ = request(
            url,
            f"orgs/{ORGANIZATION}/teams",
            password,
            method="POST",
            body={
                "name": "members",
                "includes_all_repositories": True,
                "can_create_org_repo": False,
                "units_map": {"repo.code": "read", "repo.issues": "read", "repo.pulls": "read"},
            },
        )
        if code != 201:
            raise GiteaError("Gitea member team initialization failed")
