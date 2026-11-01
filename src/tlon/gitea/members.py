"""Recoverable host-only member identities and irrevocable retirement."""

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.initialize import ORGANIZATION, request
from tlon.gitea.storage import GiteaError, write_private

TOKEN_NAME = "tlon-execution"
TOKEN_SCOPES = ["read:user", "read:organization", "write:repository", "write:issue"]


@dataclass(frozen=True, repr=False)
class MemberCredentials:
    username: str
    token: str
    email: str


def member_username(member_id: str) -> str:
    return "tlon-m-" + hashlib.sha256(member_id.encode()).hexdigest()[:32]


class MemberManager:
    """Call serially; credentials are available only after a successful reconciliation."""

    def __init__(self, paths: TlonPaths, url: str, admin_password: str) -> None:
        self._root = paths.gitea_dir() / "members"
        self._url = url
        self._password = admin_password
        self._ready: dict[str, MemberCredentials] = {}
        self._control = StartupControl()

    def credentials(self, member_id: str) -> MemberCredentials:
        try:
            return self._ready[member_id]
        except KeyError:
            raise GiteaError("Gitea member access is unavailable") from None

    def _check(self) -> None:
        try:
            self._control.check()
        except GiteaError:
            self._ready.clear()
            raise

    def _call(
        self, path: str, *, method: str = "GET", body: dict[str, object] | None = None
    ) -> tuple[int, Any]:
        self._check()
        result = request(self._url, path, self._password, method=method, body=body)
        self._check()
        return result

    def _list(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            code, batch = self._call(f"{path}?limit=50&page={page}")
            if (
                code != 200
                or not isinstance(batch, list)
                or not all(isinstance(item, dict) for item in batch)
            ):
                raise GiteaError("Gitea member lookup failed")
            result.extend(batch)
            if len(batch) < 50:
                return result
            page += 1

    def _load(self, path: Path) -> dict[str, str | int]:
        record = json.loads(path.read_text())
        if (
            not isinstance(record, dict)
            or set(record) != {"member_id", "username", "password", "state", "token", "token_id"}
            or not all(isinstance(record[key], str) for key in record if key != "token_id")
            or not isinstance(record["token_id"], int)
            or record["username"] != member_username(record["member_id"])
            or path.name != record["username"] + ".json"
            or len(record["password"]) < 32
            or record["state"] not in {"active", "retiring", "retired"}
        ):
            raise GiteaError("Invalid Gitea member record; explicit recovery required")
        path.chmod(0o600)
        return record

    def reconcile(
        self, active: Mapping[str, str], *, control: StartupControl | None = None
    ) -> dict[str, str]:
        self._control = control if control is not None else StartupControl()
        self._ready.clear()
        self._check()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)
        errors: dict[str, str] = {}
        records: dict[str, tuple[Path, dict[str, str | int]]] = {}
        # Corrupt records fail the whole pass: their retirement cannot be safely inferred.
        try:
            for path in self._root.glob("*.json"):
                record = self._load(path)
                records[str(record["member_id"])] = path, record
        except Exception:
            raise GiteaError("Invalid Gitea member storage; explicit recovery required") from None
        for member_id in active:
            if member_id not in records:
                username = member_username(member_id)
                records[member_id] = (
                    self._root / (username + ".json"),
                    {
                        "member_id": member_id,
                        "username": username,
                        "password": secrets.token_urlsafe(36),
                        "state": "active",
                        "token": "",
                        "token_id": 0,
                    },
                )
        for member_id, (path, record) in sorted(
            records.items(), key=lambda item: item[0] in active and item[1][1]["state"] == "active"
        ):
            self._check()
            try:
                if not path.exists():
                    write_private(path, record)
                if member_id not in active or record["state"] != "active":
                    self._retire(path, record)
                    if member_id in active:
                        raise GiteaError("Retired Gitea identities cannot be reactivated")
                else:
                    self._activate(path, record, active[member_id])
            except GiteaError as exc:
                self._check()
                errors[member_id] = str(exc)
            except Exception:
                self._check()
                errors[member_id] = "Gitea member reconciliation failed; retry required"
        return errors

    def _activate(self, path: Path, record: dict[str, str | int], display_name: str) -> None:
        username = str(record["username"])
        email = username + "@tlon.invalid"
        code, user = self._call(f"users/{username}")
        if code == 404:
            code, user = self._call(
                "admin/users",
                method="POST",
                body={
                    "username": username,
                    "password": record["password"],
                    "email": email,
                    "full_name": display_name,
                    "must_change_password": False,
                    "send_notify": False,
                },
            )
            if code != 201:
                raise GiteaError("Gitea member creation failed; retry required")
        elif code != 200:
            raise GiteaError("Gitea member lookup failed")
        if not isinstance(user, dict) or user.get("login") != username or user.get("is_admin"):
            raise GiteaError("Gitea member identity mismatch")
        # Password authentication proves ownership after a lost creation response.
        self._check()
        code, identity = request(self._url, "user", str(record["password"]), username=username)
        self._check()
        if code != 200 or not isinstance(identity, dict) or identity.get("login") != username:
            raise GiteaError("Gitea member authentication failed; explicit recovery required")
        if not record["token_id"] or user.get("full_name") != display_name:
            code, _ = self._call(
                f"admin/users/{username}",
                method="PATCH",
                body={
                    "source_id": 0,
                    "login_name": username,
                    "full_name": display_name,
                    "allow_create_organization": False,
                    "max_repo_creation": 0,
                },
            )
            if code != 200:
                raise GiteaError("Gitea member configuration failed")
        teams = self._list(f"orgs/{ORGANIZATION}/teams")
        team = next((item for item in teams if item.get("name") == "members"), None)
        if team is None:
            raise GiteaError("Gitea default member team missing")
        code, _ = self._call(f"teams/{team['id']}/members/{username}", method="PUT")
        if code != 204:
            raise GiteaError("Gitea member team enrollment failed")
        tokens = self._list(f"users/{username}/tokens")
        token = next((item for item in tokens if item.get("name") == TOKEN_NAME), None)
        if not (
            token is not None
            and record["token"]
            and token.get("id") == record["token_id"]
            and token.get("token_last_eight") == str(record["token"])[-8:]
            and set(token.get("scopes", [])) == set(TOKEN_SCOPES)
        ):
            if token is not None:
                code, _ = self._call(f"users/{username}/tokens/{token['id']}", method="DELETE")
                if code not in {204, 404}:
                    raise GiteaError("Gitea member token recovery failed")
            code, token = self._call(
                f"users/{username}/tokens",
                method="POST",
                body={
                    "name": TOKEN_NAME,
                    "scopes": TOKEN_SCOPES,
                },
            )
            if (
                code != 201
                or not isinstance(token, dict)
                or not token.get("sha1")
                or not token.get("id")
            ):
                raise GiteaError("Gitea member token creation failed")
            record.update(token=token["sha1"], token_id=token["id"])
            write_private(path, record)
        self._ready[str(record["member_id"])] = MemberCredentials(
            username, str(record["token"]), email
        )

    def _retire(self, path: Path, record: dict[str, str | int]) -> None:
        if record["state"] == "retired":
            return
        if record["state"] != "retiring":
            record["state"] = "retiring"
            write_private(path, record)
        username = str(record["username"])
        code, _ = self._call(
            f"admin/users/{username}",
            method="PATCH",
            body={
                "source_id": 0,
                "login_name": username,
                "prohibit_login": True,
            },
        )
        if code == 404:
            record.update(state="retired", token="", token_id=0)
            write_private(path, record)
            return
        if code != 200:
            raise GiteaError("Gitea member retirement pending: login revocation failed")
        for token in self._list(f"users/{username}/tokens"):
            code, _ = self._call(f"users/{username}/tokens/{token['id']}", method="DELETE")
            if code not in {204, 404}:
                raise GiteaError("Gitea member retirement pending: token revocation failed")
        code, _ = self._call(f"orgs/{ORGANIZATION}/members/{username}", method="DELETE")
        if code not in {204, 404}:
            raise GiteaError("Gitea member retirement pending: organization removal failed")
        record.update(state="retired", token="", token_id=0)
        write_private(path, record)
