"""Human-managed upstream repositories; contributors develop in native Gitea forks."""

import re
from typing import Any
from urllib.parse import quote

from tlon.gitea.control import StartupControl
from tlon.gitea.initialize import OPERATOR, ORGANIZATION, request
from tlon.gitea.storage import GiteaError

READ_UNITS = {"repo.code": "read", "repo.issues": "read", "repo.pulls": "read"}
REVIEW_UNITS = {**READ_UNITS, "repo.code": "write", "repo.pulls": "write"}


def _name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) or value.endswith(".git"):
        raise GiteaError("Invalid Gitea project name")
    return value


def _patterns(default: str, additional: list[str]) -> list[str]:
    result = list(dict.fromkeys([default, *additional]))
    if len(result) > 50 or any(
        not pattern
        or len(pattern) > 255
        or any(char.isspace() or ord(char) < 32 for char in pattern)
        for pattern in result
    ):
        raise GiteaError("Invalid Gitea protected branch patterns")
    return result


def _team_name(repo_id: int) -> str:
    return f"tlon-project-{repo_id}-reviewers"


def _team_options(repo_id: int) -> dict[str, object]:
    return {
        "name": _team_name(repo_id),
        "units_map": REVIEW_UNITS,
        "includes_all_repositories": False,
        "can_create_org_repo": False,
    }


def _protection(repo_id: int) -> dict[str, object]:
    return {
        "enable_push": False,
        "enable_push_whitelist": False,
        "enable_force_push": False,
        "enable_force_push_allowlist": False,
        "enable_merge_whitelist": True,
        "merge_whitelist_teams": [_team_name(repo_id)],
        "merge_whitelist_usernames": [OPERATOR],
        "block_admin_merge_override": True,
        "enable_bypass_allowlist": False,
        "unprotected_file_patterns": "",
    }


def _matches(actual: dict[str, Any], expected: dict[str, object]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


class ProjectManager:
    """Reads remote permissions on every operation; call serially through the facade."""

    def __init__(self, url: str, password: str, *, control: StartupControl | None = None) -> None:
        self._url = url
        self._password = password
        self._control = control or StartupControl()

    def _call(
        self, path: str, *, method: str = "GET", body: dict[str, object] | None = None
    ) -> tuple[int, Any]:
        self._control.check()
        try:
            result = request(self._url, path, self._password, method=method, body=body)
        except Exception:
            raise GiteaError(
                "Gitea project request failed; inspect state before retrying"
            ) from None
        self._control.check()
        return result

    def _list(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            code, batch = self._call(f"{path}?limit=50&page={page}")
            if (
                code != 200
                or not isinstance(batch, list)
                or any(not isinstance(item, dict) for item in batch)
            ):
                raise GiteaError("Gitea project lookup failed")
            result.extend(batch)
            if len(batch) < 50:
                return result
            page += 1

    def _organization(self) -> tuple[list[dict[str, Any]], set[str]]:
        code, org = self._call(f"orgs/{ORGANIZATION}")
        if code != 200 or not isinstance(org, dict) or org.get("visibility") != "private":
            raise GiteaError("Gitea project governance requires a private organization")
        teams = self._list(f"orgs/{ORGANIZATION}/teams")
        owners = next((team for team in teams if team.get("name") == "Owners"), None)
        members = next((team for team in teams if team.get("name") == "members"), None)
        if (
            owners is None
            or members is None
            or members.get("permission") in {"admin", "owner"}
            or not _matches(
                members,
                {
                    "units_map": READ_UNITS,
                    "includes_all_repositories": True,
                    "can_create_org_repo": False,
                },
            )
        ):
            raise GiteaError("Gitea organization baseline permissions conflict")
        if {user.get("login") for user in self._list(f"teams/{owners['id']}/members")} != {
            OPERATOR
        }:
            raise GiteaError("Gitea organization ownership conflicts with project governance")
        roster = self._list(f"teams/{members['id']}/members")
        if any(
            not isinstance(user.get("login"), str)
            or not re.fullmatch(r"tlon-m-[a-f0-9]{32}", user["login"])
            or user.get("is_admin") is not False
            for user in roster
        ):
            raise GiteaError("Gitea baseline member identities conflict")
        return teams, {user["login"] for user in roster}

    def _repo(self, name: str) -> dict[str, Any]:
        code, repo = self._call(f"repos/{ORGANIZATION}/{_name(name)}")
        if code != 200 or not isinstance(repo, dict):
            raise GiteaError("Gitea project not found")
        owner = repo.get("owner")
        if (
            not isinstance(owner, dict)
            or owner.get("login") != ORGANIZATION
            or repo.get("private") is not True
            or not isinstance(repo.get("id"), int)
            or not repo.get("default_branch")
            or repo.get("empty") is True
            or repo.get("archived") is True
            or repo.get("mirror") is True
        ):
            raise GiteaError("Gitea project must be an initialized private organization repository")
        return repo

    def _audit_team(
        self, repo: dict[str, Any], teams: list[dict[str, Any]], members: set[str]
    ) -> tuple[dict[str, Any] | None, list[str], list[str], bool]:
        path = f"repos/{ORGANIZATION}/{repo['name']}"
        attached = self._list(f"{path}/teams")
        problems: list[str] = []
        for item in attached:
            if item.get("name") in {"Owners", "members", _team_name(repo["id"])}:
                continue
            units = item.get("units_map")
            if (
                item.get("permission") in {"write", "admin", "owner"}
                or not isinstance(units, dict)
                or any(value not in {"read", "none"} for value in units.values())
            ):
                problems.append("Unknown elevated project team")
        if self._list(f"{path}/collaborators"):
            problems.append("Direct project collaborators require explicit recovery")
        team = next((item for item in teams if item.get("name") == _team_name(repo["id"])), None)
        if team is None:
            return None, problems, [], False
        if not _matches(team, _team_options(repo["id"])) or team.get("permission") in {
            "admin",
            "owner",
        }:
            problems.append("Project reviewer team permissions conflict")
        if any(item.get("id") != repo["id"] for item in self._list(f"teams/{team['id']}/repos")):
            problems.append("Project reviewer team spans other repositories")
        roster = self._list(f"teams/{team['id']}/members")
        if any(
            user.get("login") not in members or user.get("is_admin") is not False for user in roster
        ):
            problems.append("Project reviewer identities conflict")
        is_attached = any(item.get("id") == team["id"] for item in attached)
        if not is_attached and roster:
            problems.append("Unattached reviewer team has existing members")
        return team, problems, sorted(user["login"] for user in roster), is_attached

    def _view(
        self, repo: dict[str, Any], teams: list[dict[str, Any]], members: set[str]
    ) -> dict[str, Any]:
        team, problems, reviewers, attached = self._audit_team(repo, teams, members)
        if team is None or not attached:
            problems.append("Project reviewer team is missing or not attached")
        rules = self._list(f"repos/{ORGANIZATION}/{repo['name']}/branch_protections")
        patterns = [rule.get("rule_name", "") for rule in rules]
        if repo["default_branch"] not in patterns:
            problems.append("Default branch protection is missing")
        if any(not _matches(rule, _protection(repo["id"])) for rule in rules):
            problems.append("Branch protection permissions conflict")
        return {
            "name": repo["name"],
            "html_url": repo["html_url"],
            "default_branch": repo["default_branch"],
            "protected_branches": patterns,
            "reviewers": reviewers,
            "managed": not problems,
            "problems": problems,
        }

    def list_projects(self) -> list[dict[str, Any]]:
        teams, members = self._organization()
        result = []
        for repo in self._list(f"orgs/{ORGANIZATION}/repos"):
            try:
                checked = self._repo(repo["name"])
            except GiteaError as exc:
                result.append(
                    {
                        "name": repo["name"],
                        "html_url": repo.get("html_url", ""),
                        "default_branch": repo.get("default_branch", ""),
                        "protected_branches": [],
                        "reviewers": [],
                        "managed": False,
                        "problems": [str(exc)],
                    }
                )
            else:
                result.append(self._view(checked, teams, members))
        return result

    def configure(
        self,
        name: str,
        *,
        create: bool,
        default_branch: str = "main",
        protected_branches: list[str],
    ) -> dict[str, Any]:
        name = _name(name)
        _patterns(default_branch, protected_branches)
        teams, members = self._organization()
        code, _ = self._call(f"repos/{ORGANIZATION}/{name}")
        if code == 404 and create:
            code, _ = self._call(
                f"orgs/{ORGANIZATION}/repos",
                method="POST",
                body={
                    "name": name,
                    "private": True,
                    "auto_init": True,
                    "default_branch": default_branch,
                },
            )
            if code not in {201, 409}:
                raise GiteaError("Gitea project creation failed; inspect state before retrying")
        repo = self._repo(name)
        patterns = _patterns(repo["default_branch"], protected_branches)
        team, problems, _, _ = self._audit_team(repo, teams, members)
        if problems:
            raise GiteaError("; ".join(problems))
        path = f"repos/{ORGANIZATION}/{name}/branch_protections"
        rules = self._list(path)
        if any(
            rule.get("rule_name") not in patterns and not _matches(rule, _protection(repo["id"]))
            for rule in rules
        ):
            raise GiteaError("Unrequested branch protection conflicts; explicit recovery required")
        if team is None:
            code, team = self._call(
                f"orgs/{ORGANIZATION}/teams", method="POST", body=_team_options(repo["id"])
            )
            if code != 201 or not isinstance(team, dict):
                raise GiteaError("Gitea reviewer team creation failed; retry required")
        code, _ = self._call(f"teams/{team['id']}/repos/{ORGANIZATION}/{name}", method="PUT")
        if code != 204:
            raise GiteaError("Gitea reviewer team attachment failed; retry required")
        existing = {rule["rule_name"] for rule in rules}
        for pattern in patterns:
            code, _ = self._call(
                path + ("/" + quote(pattern, safe="") if pattern in existing else ""),
                method="PATCH" if pattern in existing else "POST",
                body={"rule_name": pattern, **_protection(repo["id"])},
            )
            if code not in {200, 201}:
                raise GiteaError("Gitea branch protection configuration failed; retry required")
        # Explicitly replace the protected set only after the new protections exist.
        for pattern in existing - set(patterns):
            code, _ = self._call(path + "/" + quote(pattern, safe=""), method="DELETE")
            if code not in {204, 404}:
                raise GiteaError("Gitea obsolete protection removal failed; retry required")
        result = self._view(self._repo(name), *self._organization())
        if not result["managed"] or set(result["protected_branches"]) != set(patterns):
            raise GiteaError("Gitea project configuration verification failed")
        return result

    def set_reviewer(self, name: str, username: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool) or not re.fullmatch(r"tlon-m-[a-f0-9]{32}", username):
            raise GiteaError("Reviewer access requires a managed member identity and boolean grant")
        teams, members = self._organization()
        if username not in members:
            raise GiteaError("Reviewer access requires an active organization member")
        repo = self._repo(name)
        if not self._view(repo, teams, members)["managed"]:
            raise GiteaError("Gitea project permissions conflict; configure the project first")
        team = next(item for item in teams if item.get("name") == _team_name(repo["id"]))
        code, _ = self._call(
            f"teams/{team['id']}/members/{username}", method="PUT" if enabled else "DELETE"
        )
        if code not in ({204} if enabled else {204, 404}):
            raise GiteaError("Gitea reviewer access change failed; retry required")
        result = self._view(self._repo(name), *self._organization())
        if not result["managed"] or (username in result["reviewers"]) != enabled:
            raise GiteaError("Gitea reviewer access verification failed; retry required")
        return result
