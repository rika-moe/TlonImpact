"""One-time, resumable creation of the packaged initial society (ADR 0036)."""

import shutil
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.identifiers import new_member_id
from tlon.base.paths import TlonPaths


class _Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    background: str = Field(min_length=1)
    organization: str | None = None


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    prototype: str = Field(pattern=r"^p-[a-f0-9]{32}$")
    members: list[_Member] = Field(min_length=1)
    relations: list[tuple[str, str]]

    @model_validator(mode="after")
    def validate_graph(self) -> "_Manifest":
        keys = {member.key for member in self.members}
        if len(keys) != len(self.members):
            raise ValueError("duplicate initial member key")
        edges = {frozenset(edge) for edge in self.relations}
        if len(edges) != len(self.relations) or any(
            len(edge) != 2 or not edge <= keys for edge in edges
        ):
            raise ValueError("invalid initial society relations")
        return self


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "complete", "skipped"]
    manifest: _Manifest | None = None
    addresses: dict[str, str] = Field(default_factory=dict)
    personas: dict[str, dict[str, str]] = Field(default_factory=dict)
    staged: bool = False

    @model_validator(mode="after")
    def validate_plan(self) -> "_State":
        if self.status != "skipped":
            if self.manifest is None:
                raise ValueError("missing initial society manifest")
            keys = {m.key for m in self.manifest.members}
            if set(self.addresses) != keys or set(self.personas) != keys:
                raise ValueError("incomplete initial society plan")
            if len(set(self.addresses.values())) != len(keys):
                raise ValueError("duplicate initial society address")
            for address in self.addresses.values():
                if not address.startswith("m-") or len(address) != 34:
                    raise ValueError("invalid initial society address")
                int(address[2:], 16)
            for persona in self.personas.values():
                if set(persona) != {"IDENTITY", "SOUL", "RULES"} or not all(persona.values()):
                    raise ValueError("incomplete initial persona")
        return self


class _StagingPaths(TlonPaths):
    def __init__(self, paths: TlonPaths) -> None:
        super().__init__(paths.initial_society_staging_dir())
        self._prototype_root = paths.prototypes_dir()

    def prototypes_dir(self) -> Path:
        return self._prototype_root


def _read_state(paths: TlonPaths) -> _State | None:
    path = paths.initial_society_state_file()
    if not path.exists():
        return None
    try:
        return _State.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, ValidationError) as exc:
        raise RuntimeError(f"初始社会状态损坏：{path}") from exc


def _save_state(paths: TlonPaths, state: _State) -> None:
    target = paths.initial_society_state_file()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def _existing_society(paths: TlonPaths) -> bool:
    if paths.mail_database_file().exists() or paths.webui_identity_file().exists():
        return True
    # An invalid or retired member still proves this is an existing society.
    for entry in paths.tlon_home().iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if paths.agent_config_file(entry.name).exists():
            return True
        if entry.name.startswith(("m-", "human-")) and any(entry.iterdir()):
            return True
        if any(
            path.exists()
            for path in (
                paths.agent_program_database_file(entry.name),
                paths.agent_tasks_file(entry.name),
                paths.agent_ledger_file(entry.name),
                paths.agent_mail_dir(entry.name),
                paths.agent_config_dir(entry.name),
                paths.agent_workspace_dir(entry.name),
                paths.agent_dir(entry.name) / "memory",  # Existing archive is not a fresh society.
            )
        ):
            return True
    return False


def _new_plan() -> _State:
    package = resources.files("tlon.initial_society")
    manifest = _Manifest.model_validate_json(package.joinpath("manifest.json").read_text("utf-8"))
    return _State(
        status="pending",
        manifest=manifest,
        addresses={member.key: new_member_id() for member in manifest.members},
        personas={
            member.key: {
                kind: package.joinpath(member.key, f"{kind}.md").read_text("utf-8")
                for kind in ("IDENTITY", "SOUL", "RULES")
            }
            for member in manifest.members
        },
    )


def _contacts(member: _Member, state: _State) -> str:
    assert state.manifest is not None
    neighbors = {
        b if a == member.key else a for a, b in state.manifest.relations if member.key in (a, b)
    }
    sections = ["# 联系簿"]
    for other in state.manifest.members:
        if other.key not in neighbors:
            continue
        relation = "初始相识，已有基本了解，尚未实际通信。"
        if member.organization and member.organization == other.organization:
            relation = (
                f"同属{member.organization}，已有初始合作约定，"
                "愿意共同探索；尚未实际通信或产生成果，双方均可退出或另组。"
            )
        sections.append(
            f"## {other.name}\n- 地址：{state.addresses[other.key]}@tlon.internal\n"
            f"- 描述：{other.background}。{relation}"
        )
    return "\n\n".join(sections) + "\n"


def _stage_members(paths: TlonPaths, state: _State) -> None:
    assert state.manifest is not None
    staging = _StagingPaths(paths)
    # An interrupted staging pass has published nothing; rebuild it from the saved plan.
    if staging.tlon_home().exists():
        shutil.rmtree(staging.tlon_home())
    for member in state.manifest.members:
        address = state.addresses[member.key]
        instantiate(
            state.manifest.prototype,
            address,
            member.name,
            model=state.manifest.model,
            tlon_paths=staging,
        )
        for kind, content in state.personas[member.key].items():
            (staging.agent_config_dir(address) / f"{kind}.md").write_text(content, "utf-8")
        staging.agent_workspace_dir(address).mkdir(parents=True, exist_ok=True)
        staging.agent_contacts_file(address).write_text(_contacts(member, state), "utf-8")
        (staging.agent_dir(address) / ".initial-society-key").write_text(member.key, "utf-8")
    state.staged = True
    _save_state(paths, state)


def _publish_member(paths: TlonPaths, key: str, address: str) -> None:
    source = _StagingPaths(paths).agent_dir(address)
    target = paths.agent_dir(address)
    if target.exists():
        marker = target / ".initial-society-key"
        if source.exists() or not marker.is_file() or marker.read_text("utf-8") != key:
            raise RuntimeError(f"初始社会成员目录冲突：{target}")
        return
    if not source.is_dir():
        raise RuntimeError(f"初始社会暂存成员丢失：{source}")
    source.rename(target)


def seed_initial_society(paths: TlonPaths) -> int:
    """Seed only fresh homes; serialize concurrent initializers and resume saved plans."""
    state = _read_state(paths)
    if state is not None and state.status != "pending":
        return 0
    paths.tlon_home().mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(paths.initial_society_lock_file(), timeout=60)
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = _read_state(paths)
        if state is not None and state.status != "pending":
            return 0
        if state is None:
            state = _State(status="skipped") if _existing_society(paths) else _new_plan()
            _save_state(paths, state)
        if state.status == "skipped":
            return 0
        if not state.staged:
            _stage_members(paths, state)
        for key, address in state.addresses.items():
            _publish_member(paths, key, address)
        state.status = "complete"
        _save_state(paths, state)
        shutil.rmtree(paths.initial_society_staging_dir(), ignore_errors=True)
        return len(state.addresses)
    finally:
        connection.close()
