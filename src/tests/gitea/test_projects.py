"""Fork contributors stay read-only upstream; only reviewers receive upstream write."""

from copy import deepcopy
from typing import Any
from urllib.parse import unquote

import pytest

from tlon.gitea import projects
from tlon.gitea.projects import ProjectManager
from tlon.gitea.storage import GiteaError

MEMBER = "tlon-m-" + "a" * 32


class FakeGitea:
    def __init__(self) -> None:
        self.repos: dict[str, dict[str, Any]] = {}
        self.teams: dict[int, dict[str, Any]] = {
            1: {"id": 1, "name": "Owners"},
            2: {
                "id": 2,
                "name": "members",
                "units_map": projects.READ_UNITS,
                "includes_all_repositories": True,
                "can_create_org_repo": False,
            },
        }
        self.members = {1: {"tlon-operator"}, 2: {MEMBER}}
        self.attachments: dict[int, set[str]] = {1: set(), 2: set()}
        self.rules: dict[str, dict[str, dict[str, Any]]] = {}
        self.collaborators: list[dict[str, str]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.failure: tuple[str, str] | None = None
        self.visibility = "private"
        self.admins: set[str] = set()

    def request(
        self,
        url: str,
        path: str,
        password: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> tuple[int, Any]:
        path = path.split("?")[0]
        self.calls.append((method, path, deepcopy(body)))
        if (method, path) == self.failure:
            return 500, None
        parts = path.split("/")
        if path == "orgs/tlon":
            return 200, {"visibility": self.visibility}
        if path == "orgs/tlon/teams":
            if method == "POST":
                team_id = max(self.teams) + 1
                self.teams[team_id] = {"id": team_id, **(body or {})}
                self.members[team_id] = set()
                self.attachments[team_id] = set()
                return 201, deepcopy(self.teams[team_id])
            return 200, deepcopy(list(self.teams.values()))
        if path == "orgs/tlon/repos":
            if method == "POST":
                assert body
                name = str(body["name"])
                self.repos[name] = {
                    "id": len(self.repos) + 10,
                    "owner": {"login": "tlon"},
                    "html_url": f"http://localhost/tlon/{name}",
                    **body,
                }
                self.rules[name] = {}
                return 201, deepcopy(self.repos[name])
            return 200, deepcopy(list(self.repos.values()))
        if parts[0] == "teams":
            team_id = int(parts[1])
            if parts[2] == "members":
                if len(parts) == 3:
                    return 200, [
                        {"login": login, "is_admin": login in self.admins}
                        for login in self.members[team_id]
                    ]
                login = parts[3]
                if method == "PUT":
                    self.members[team_id].add(login)
                    return 204, None
                if method == "DELETE":
                    self.members[team_id].discard(login)
                    return 204, None
            if parts[2] == "repos":
                if method == "PUT":
                    self.attachments[team_id].add(parts[4])
                    return 204, None
                return 200, [deepcopy(self.repos[name]) for name in self.attachments[team_id]]
        if parts[:2] == ["repos", "tlon"]:
            name = parts[2]
            if name not in self.repos:
                return 404, None
            if len(parts) == 3:
                return 200, deepcopy(self.repos[name])
            if parts[3] == "teams":
                return 200, [
                    deepcopy(team)
                    for team_id, team in self.teams.items()
                    if team_id in {1, 2} or name in self.attachments[team_id]
                ]
            if parts[3] == "collaborators":
                return 200, deepcopy(self.collaborators)
            if parts[3] == "branch_protections":
                if method == "DELETE":
                    self.rules[name].pop(unquote(parts[4]), None)
                    return 204, None
                if method in {"POST", "PATCH"}:
                    assert body
                    pattern = str(body["rule_name"])
                    if method == "PATCH":
                        assert unquote(parts[4]) == pattern
                    self.rules[name].setdefault(pattern, {}).update(deepcopy(body))
                    return 201 if method == "POST" else 200, deepcopy(self.rules[name][pattern])
                return 200, deepcopy(list(self.rules[name].values()))
        raise AssertionError((method, path, body))


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeGitea:
    fake = FakeGitea()
    monkeypatch.setattr(projects, "request", fake.request)
    return fake


def configured(api: FakeGitea) -> ProjectManager:
    client = ProjectManager("http://localhost/", "host-secret")
    client.configure("example", create=True, protected_branches=[])
    return client


def test_only_reviewer_team_and_explicit_protections(api: FakeGitea) -> None:
    client = configured(api)
    assert len(api.teams) == 3
    assert api.teams[3]["name"] == "tlon-project-10-reviewers"
    assert api.teams[3]["units_map"] == {
        "repo.code": "write",
        "repo.pulls": "write",
        "repo.issues": "read",
    }
    assert api.teams[2]["units_map"] == projects.READ_UNITS
    assert set(api.rules["example"]) == {"main"}
    assert api.rules["example"]["main"]["merge_whitelist_teams"] == ["tlon-project-10-reviewers"]
    assert client.set_reviewer("example", MEMBER, True)["reviewers"] == [MEMBER]
    assert client.set_reviewer("example", MEMBER, False)["reviewers"] == []
    api.members[3].add(MEMBER)
    assert client.list_projects()[0]["reviewers"] == [MEMBER]


def test_configuration_reuses_resources_and_protects_actual_default(api: FakeGitea) -> None:
    client = configured(api)
    api.repos["example"]["default_branch"] = "trunk"
    result = client.configure(
        "example",
        create=True,
        default_branch="ignored",
        protected_branches=["develop", "release/**"],
    )
    assert set(result["protected_branches"]) == {"trunk", "develop", "release/**"}
    assert len(api.repos) == 1 and len(api.teams) == 3


@pytest.mark.parametrize("name", ["../repo", "repo/name", "repo?name", "repo.git", "", "a\n"])
def test_invalid_name_never_contacts_service(api: FakeGitea, name: str) -> None:
    with pytest.raises(GiteaError, match="Invalid"):
        ProjectManager("url", "secret").configure(name, create=True, protected_branches=[])
    assert not api.calls


@pytest.mark.parametrize(
    "conflict",
    [
        "owner",
        "public",
        "collaborator",
        "foreign_team",
        "team_scope",
        "baseline_admin",
        "baseline_outsider",
        "reviewer_outsider",
    ],
)
def test_conflicting_authority_is_never_silently_repaired(api: FakeGitea, conflict: str) -> None:
    client = configured(api)
    if conflict == "owner":
        api.members[1].add(MEMBER)
    elif conflict == "public":
        api.visibility = "public"
    elif conflict == "collaborator":
        api.collaborators.append({"login": MEMBER})
    elif conflict == "foreign_team":
        api.teams[4] = {"id": 4, "name": "strangers", "permission": "admin"}
        api.attachments[4] = {"example"}
    elif conflict == "team_scope":
        api.teams[3]["includes_all_repositories"] = True
    elif conflict == "baseline_admin":
        api.admins.add(MEMBER)
    elif conflict == "baseline_outsider":
        api.members[2].add("outsider")
    else:
        api.members[3].add("outsider")
    api.calls.clear()
    with pytest.raises(GiteaError):
        client.configure("example", create=False, protected_branches=[])
    with pytest.raises(GiteaError):
        client.set_reviewer("example", MEMBER, True)
    assert all(method == "GET" for method, _, _ in api.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("enable_push", True),
        ("enable_push_whitelist", True),
        ("enable_force_push", True),
        ("enable_bypass_allowlist", True),
        ("block_admin_merge_override", False),
        ("unprotected_file_patterns", "*"),
    ],
)
def test_protection_drift_is_visible_and_blocks_reviewer_grants(
    api: FakeGitea, field: str, value: Any
) -> None:
    client = configured(api)
    api.rules["example"]["main"][field] = value
    api.calls.clear()
    assert client.list_projects()[0]["managed"] is False
    with pytest.raises(GiteaError, match="permissions conflict"):
        client.set_reviewer("example", MEMBER, True)
    assert all(method == "GET" for method, _, _ in api.calls)


def test_explicit_protection_update_preserves_review_requirements(api: FakeGitea) -> None:
    client = configured(api)
    client.configure("example", create=False, protected_branches=["staging"])
    api.rules["example"]["main"]["required_approvals"] = 2
    api.calls.clear()
    result = client.configure("example", create=False, protected_branches=["production/**"])
    assert set(result["protected_branches"]) == {"main", "production/**"}
    assert api.rules["example"]["main"]["required_approvals"] == 2
    changes = [method for method, _, _ in api.calls if method != "GET"]
    assert changes.index("POST") < changes.index("DELETE")


def test_unsafe_unrequested_rule_is_not_removed(api: FakeGitea) -> None:
    client = configured(api)
    api.rules["example"]["danger"] = {"rule_name": "danger", "enable_push": True}
    with pytest.raises(GiteaError, match="Unrequested"):
        client.configure("example", create=False, protected_branches=[])
    assert "danger" in api.rules["example"]


def test_partial_creation_retries_without_duplicate_resources(api: FakeGitea) -> None:
    api.failure = ("PUT", "teams/3/repos/tlon/example")
    with pytest.raises(GiteaError, match="attachment failed"):
        configured(api)
    api.failure = None
    assert configured(api).list_projects()[0]["managed"]
    assert len(api.teams) == 3 and len(api.repos) == 1


def test_unattached_existing_roster_is_not_given_repository_access(api: FakeGitea) -> None:
    client = configured(api)
    api.attachments[3].clear()
    api.members[3].add(MEMBER)
    with pytest.raises(GiteaError, match="Unattached"):
        client.configure("example", create=False, protected_branches=[])
    assert api.attachments[3] == set()


def test_failed_rule_creation_keeps_previous_protection(api: FakeGitea) -> None:
    client = configured(api)
    client.configure("example", create=False, protected_branches=["staging"])
    api.failure = ("POST", "repos/tlon/example/branch_protections")
    with pytest.raises(GiteaError, match="configuration failed"):
        client.configure("example", create=False, protected_branches=["production"])
    assert "staging" in api.rules["example"]


def test_nonmember_cannot_receive_reviewer_access(api: FakeGitea) -> None:
    client = configured(api)
    api.members[2].remove(MEMBER)
    with pytest.raises(GiteaError, match="active organization"):
        client.set_reviewer("example", MEMBER, True)


def test_request_errors_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("host-secret")

    monkeypatch.setattr(projects, "request", fail)
    with pytest.raises(GiteaError) as caught:
        ProjectManager("url", "host-secret").list_projects()
    assert "host-secret" not in str(caught.value)


def test_all_pages_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def paginated(url: str, path: str, password: str, **kwargs: Any) -> tuple[int, Any]:
        return 200, [{"id": i} for i in range(50)] if "page=1" in path else [{"id": 50}]

    monkeypatch.setattr(projects, "request", paginated)
    assert len(ProjectManager("url", "password")._list("teams/3/members")) == 51
