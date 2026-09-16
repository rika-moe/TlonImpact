"""Behavior prototype library (stage 5 of docs/design/dsl-agents.md).

A prototype is a directory of pure data — the behavior genome (unified
member model, ADR 0006: prototypes carry **no persona**; a member's
persona files are creation-time data, never part of the genome):

- ``prototype.edn`` — metadata ``{:name :display-name :description
  :trusted :author :parent :generation :proposal-note}``
- ``behavior.tlon`` — a complete (total) member spec, unified rules form
  (``{:version 1 :rules [...]}``, phase C — the old ``script.tlon``
  ScriptSpec file is gone)
- ``LESSONS.md`` — optional (issue ): lessons distilled from a
  retired member's memory, packaged with the prototype and copied to the
  member's ``config/`` at instantiation; the DSL ``(lessons)`` context
  section injects it
- ``REVIEW.md`` — optional (issue , heterogeneous review): peer-review
  records appended by ``reviewPrototype`` while the prototype is staged.
  Metadata reading never touches it, so listing stays free; approval's
  whole-directory move carries it into the trusted library.

A directory qualifies as a prototype when it contains ``behavior.tlon``.
The member type (cognitive ``react`` vs deterministic ``script``) is no
longer a metadata keyword — it is **derived** from the spec's rules
(any rule with a ``:loop`` ⇒ react), so ``prototype.edn`` never declares
a runtime.

Built-in prototypes ship inside the package (``tlon/prototypes/``) and are
seeded to ``~/.tlon/prototypes/`` on startup; seeding never overwrites
existing files, so users may edit or add prototypes. ``list_prototypes``
reads the seeded directory (user modifications stay visible).

Trust model (stage 4a, the "code signing" analogy): package-seeded and
user-installed prototypes are trusted (``:trusted true``, directly
spawnable). LLM-authored prototypes are written to the staging area
(``~/.tlon/prototypes-staging/``) with ``:trusted false`` and can only be
spawned after a human approves them in the mirror, which moves them into
the trusted library.

A ``behavior.tlon`` may declare a top-level ``:parameters`` map (a
write-side template, e.g. relay's ``{:to {:type :string-list}}``) and
reference the variables as ``${to}`` inside rule forms. Instantiation
renders the template with the caller's values before writing the instance
file; the runtime never sees placeholders.

Instantiation **copies** rather than references: the behavior file is
copied verbatim, so an agent's behavior is frozen at creation time. There
are no overrides — a spec is total, and the prototype's file is the whole
behavior.
"""

import difflib
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from tlon.agents.behavior_template import (
    BehaviorTemplateError,
    ParameterSpec,
    analyze,
    render_template,
)
from tlon.agents.edn import EDNParseError, Keyword, dumps, loads
from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlon.base.config import AgentConfig, LLMConfig, yaml_exists, yaml_save
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.tools.prototypes.ports import PrototypeError

logger = get_logger(__name__)

PROTOTYPE_PACKAGE = "tlon.prototypes"
METADATA_FILE = "prototype.edn"
BEHAVIOR_FILE = "behavior.tlon"
LESSONS_FILE = "LESSONS.md"
REVIEWS_FILE = "REVIEW.md"


@dataclass(frozen=True)
class PrototypeInfo:
    """Metadata of one behavior prototype.

    ``trusted`` defaults to True for user-installed prototypes (handwritten
    implies trusted); only the staging area writes ``:trusted false``.
    ``runtime`` is **derived** from the behavior file's rules (phase C: any
    rule with a ``:loop`` ⇒ ``"react"``, else ``"script"``) — it is never a
    metadata keyword anymore. ``parameters`` (phase F) is the template's
    declared ``:parameters`` (empty for a concrete behavior), so callers
    can prompt for values before instantiation.

    Lineage (issue ): ``parent`` names the prototype this one was
    derived from (None = root), ``generation`` is 1 for roots and the
    parent's generation + 1 otherwise, ``proposal_note`` is the free-text
    rationale for this generation's change. Files predating the field read
    as roots (parent=None, generation=1) — no migration.
    """

    name: str
    display_name: str
    description: str
    trusted: bool = True
    author: str | None = None
    parent: str | None = None
    generation: int = 1
    proposal_note: str | None = None
    parameters: tuple[ParameterSpec, ...] = ()

    @property
    def has_parameters(self) -> bool:
        """Whether instantiation requires template parameter values."""
        return bool(self.parameters)


def seed_prototypes(tlon_paths: TlonPaths | None = None) -> int:
    """Copy built-in prototypes to ``~/.tlon/prototypes/`` (idempotent).

    Existing files are never overwritten: user edits and user-added
    prototypes survive re-seeding.

    Returns:
        Number of files copied (0 when everything already exists).
    """
    p = tlon_paths or paths
    target_root = p.prototypes_dir()
    copied = 0
    for entry in resources.files(PROTOTYPE_PACKAGE).iterdir():
        # __pycache__ appears when running from a source tree
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        target_dir = target_root / entry.name
        for resource in entry.iterdir():
            if not resource.is_file() or resource.name.startswith(("_", ".")):
                continue
            target = target_dir / resource.name
            if target.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resource.read_bytes())
            copied += 1
    logger.info("prototypes_seeded", copied=copied, dir=str(target_root))
    return copied


def list_prototypes(tlon_paths: TlonPaths | None = None) -> list[PrototypeInfo]:
    """List prototypes from ``~/.tlon/prototypes/`` (not the package).

    A directory qualifies when it contains a ``behavior.tlon``; metadata is
    read from ``prototype.edn`` when present, with a name-only fallback.
    """
    return _list_from((tlon_paths or paths).prototypes_dir())


def _list_from(root: Path) -> list[PrototypeInfo]:
    """List qualifying prototype directories under ``root``."""
    if not root.exists():
        return []
    result = []
    for proto_dir in sorted(root.iterdir()):
        if not proto_dir.is_dir() or not _is_prototype_dir(proto_dir):
            continue
        result.append(_read_metadata(proto_dir))
    return result


def _is_prototype_dir(proto_dir: Path) -> bool:
    """A directory qualifies when it carries the unified behavior file."""
    return (proto_dir / BEHAVIOR_FILE).exists()


def _find_prototype_dir(name: str, p: TlonPaths) -> Path | None:
    """Locate a prototype by name in either area (trusted first, then staging)."""
    for root in (p.prototypes_dir(), p.prototypes_staging_dir()):
        proto_dir = root / name
        if _is_prototype_dir(proto_dir):
            return proto_dir
    return None


def _resolve_parent_generation(parent: str, p: TlonPaths) -> int:
    """Validate a lineage parent and derive the child's generation.

    The parent may live in either area (trusted or staging) — a staged
    proposal can be the parent of a further proposal.

    Raises:
        PrototypeError: invalid parent name or parent not found
    """
    _validate_name(parent)
    parent_dir = _find_prototype_dir(parent, p)
    if parent_dir is None:
        raise PrototypeError(f"parent prototype '{parent}' not found")
    return _read_metadata(parent_dir).generation + 1


def get_prototype_info(
    name: str, tlon_paths: TlonPaths | None = None
) -> PrototypeInfo | None:
    """Metadata of a trusted-library prototype, or None when absent."""
    proto_dir = (tlon_paths or paths).prototypes_dir() / name
    if not _is_prototype_dir(proto_dir):
        return None
    return _read_metadata(proto_dir)


def instantiate(
    prototype_name: str,
    agent_id: str,
    agent_name: str,
    *,
    model: str,
    params: dict[str, Any] | None = None,
    tlon_paths: TlonPaths | None = None,
) -> AgentConfig:
    """Create an agent from a prototype: copy, no merging.

    Persona files are copied verbatim; the prototype ``behavior.tlon`` —
    a complete, total spec in the unified rules form — is copied verbatim
    as the instance's own behavior file.

    When the prototype's spec declares ``:parameters`` (a parameterized
    write-side template), ``params`` must provide concrete values; the
    file is rendered with those values before writing to disk. Otherwise
    ``params`` must be empty.

    ``agent.yaml`` is written via ``yaml_save`` (the standard config save
    path). The new agent takes effect without a restart: the caller
    (ops admin API / registry spawn) hot-adopts it — the caller
    requests ``POST /admin/config/agents/{id}/reload`` →
    ``AgentRegistry.adopt_agent``); when no server is running, it takes
    effect after the next server restart.

    Args:
        prototype_name: prototype directory name under ~/.tlon/prototypes/
        agent_id: new agent id (must not already exist)
        agent_name: display name of the new agent
        model: the member's LLM in ``provider/model`` form (ADR 0016 —
            model selection is deployment policy, not genotype; it lands
            in agent.yaml as ``llm.model_name`` and assembly hard-fails
            without it)
        params: template parameter values (required when the prototype
            declares ``:parameters``; must be empty otherwise)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        The saved AgentConfig (with ``prototype`` recording the source).

    Raises:
        PrototypeError: prototype missing, agent id already taken, model
            empty, or required params missing / invalid.
    """
    p = tlon_paths or paths
    if not model or not model.strip():
        raise PrototypeError(
            "model is required (provider/model 形式，如 deepseek/deepseek-chat)——"
            "成员级 LLM 配置没有进程级兜底（ADR 0016）"
        )
    proto_dir = p.prototypes_dir() / prototype_name
    if not _is_prototype_dir(proto_dir):
        raise PrototypeError(
            f"prototype '{prototype_name}' not found under {p.prototypes_dir()}"
        )
    if yaml_exists(agent_id, p.tlon_home()):
        raise PrototypeError(f"agent '{agent_id}' already exists")

    behavior_file = proto_dir / BEHAVIOR_FILE
    raw_text = behavior_file.read_text(encoding="utf-8")

    info = analyze(raw_text)
    if info.has_parameters:
        if params is None:
            raise PrototypeError(
                f"prototype '{prototype_name}' declares "
                f":parameters {sorted(info.placeholders)!r} — "
                f"params must be provided at instantiation"
            )
        try:
            behavior_text = render_template(raw_text, params)
        except BehaviorTemplateError as e:
            raise PrototypeError(
                f"prototype '{prototype_name}' parameter error: {e}"
            ) from e
    else:
        if params:
            raise PrototypeError(
                f"prototype '{prototype_name}' does not declare "
                f":parameters, but params were provided"
            )
        behavior_text = raw_text

    # Behavior only — copy, not reference (frozen at creation time; a
    # spec is total, so there is nothing to merge). Unified member model
    # (ADR 0006): prototypes carry no persona — a member's persona files
    # are creation-time data (wizard role cards / future tools), not part
    # of the behavior genome.
    config_dir = p.agent_config_dir(agent_id)
    (config_dir / BEHAVIOR_FILE).write_text(behavior_text, encoding="utf-8")

    # Lessons inheritance (issue ): a prototype's LESSONS.md — lessons
    # distilled from a retired member — is copied next to the behavior file
    # (a persona-file neighbor); the DSL ``(lessons)`` section injects it.
    # No LESSONS.md in the prototype → no file on the member either.
    prototype_lessons = proto_dir / LESSONS_FILE
    if prototype_lessons.exists():
        (config_dir / LESSONS_FILE).write_text(
            prototype_lessons.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # The rendered spec is parsed once here as a validation gate (the
    # loader's full validation still gates the member at boot). The spec's
    # top-level ``:llm`` declaration is parsed but deliberately NOT read
    # (ADR 0016): model selection is deployment policy, not genotype —
    # the caller's ``model`` argument is the only source, landing in
    # agent.yaml as ``llm.model_name``. (Removing the ``:llm`` key from
    # the spec grammar is a separate follow-up.)
    try:
        MemberSpec.from_edn(loads(behavior_text))
    except (EDNParseError, MemberSpecValidationError) as e:
        raise PrototypeError(
            f"prototype '{prototype_name}' behavior failed to parse: {e}"
        ) from e

    # agent.yaml via the same save path as the wizard
    agent = AgentConfig(
        id=agent_id,
        name=agent_name,
        prototype=prototype_name,
        llm=LLMConfig(model_name=model.strip()),
    )
    yaml_save(agent, p.tlon_home())
    logger.info(
        "prototype_instantiated",
        prototype=prototype_name,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    return agent


def _read_parameters(proto_dir: Any) -> tuple[ParameterSpec, ...]:
    """The behavior template's declared ``:parameters`` (empty if concrete).

    Metadata reading must never fail a listing: an unparseable behavior
    yields no parameters.
    """
    behavior_file = proto_dir / BEHAVIOR_FILE
    if not behavior_file.exists():
        return ()
    try:
        return analyze(behavior_file.read_text(encoding="utf-8")).parameters
    except Exception as e:
        logger.warning("prototype_parameters_unreadable", path=str(behavior_file), error=str(e))
        return ()


def _read_metadata(proto_dir: Any) -> PrototypeInfo:
    """Read ``prototype.edn`` metadata, falling back to the directory name.

    No runtime is derived or declared (ADR 0006, unified member model):
    a prototype is a behavior template, not a member class — whether a
    spawned member gets the cognitive engine follows from its spec.
    """
    name = proto_dir.name
    parameters = _read_parameters(proto_dir)
    metadata_file = proto_dir / METADATA_FILE
    if not metadata_file.exists():
        return PrototypeInfo(
            name=name,
            display_name=name,
            description="",
            parameters=parameters,
        )
    try:
        data = loads(metadata_file.read_text(encoding="utf-8"))
        author = data.get("author")
        parent = data.get("parent")
        note = data.get("proposal-note")
        generation = data.get("generation")
        return PrototypeInfo(
            name=str(data.get("name") or name),
            display_name=str(data.get("display-name") or data.get("display_name") or name),
            description=str(data.get("description") or ""),
            trusted=bool(data.get("trusted", True)),
            author=str(author) if author else None,
            parent=str(parent) if parent else None,
            generation=generation
            if isinstance(generation, int) and not isinstance(generation, bool)
            else 1,
            proposal_note=str(note) if note else None,
            parameters=parameters,
        )
    except Exception as e:
        logger.warning("prototype_metadata_invalid", path=str(metadata_file), error=str(e))
        return PrototypeInfo(name=name, display_name=name, description="")


# ==================== Staging area (stage 4a trust gate) ====================


def _validate_behavior(behavior_edn: str) -> str:
    """Run the write-side validation chain; return the normalized spec text.

    The gate runs the same chain a runtime load would — template analysis,
    EDN parse, then the single validator (host-less default registry: host
    predicates are Adam's vocabulary and must not pass).

    Args:
        behavior_edn: raw EDN text of the unified behavior spec

    Returns:
        The spec text normalized to end with a newline.

    Raises:
        PrototypeError: the spec failed template / EDN / schema validation
    """
    spec_text = behavior_edn if behavior_edn.endswith("\n") else behavior_edn + "\n"
    try:
        template = analyze(spec_text)
        validate_member_spec(MemberSpec.from_edn(loads(template.standin_text)))
    except BehaviorTemplateError as e:
        raise PrototypeError(f"behavior template error: {e}") from e
    except EDNParseError as e:
        raise PrototypeError(f"behavior EDN parse failed: {e}") from e
    except MemberSpecValidationError as e:
        raise PrototypeError(f"behavior spec validation failed: {e}") from e
    return spec_text


def write_staging_prototype(
    name: str,
    behavior_edn: str,
    display_name: str = "",
    description: str = "",
    author_id: str = "",
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    tlon_paths: TlonPaths | None = None,
) -> PrototypeInfo:
    """Write an LLM-authored prototype to the staging area (untrusted).

    Validation happens **before** anything touches disk.

    Both rule forms are accepted (a cognitive rule carrying a ``:loop``,
    or loop-less deterministic rules); the output is a single unified
    rules-form ``behavior.tlon`` — prototypes carry no persona (unified
    member model, ADR 0006); a member's persona files are creation-time
    data, not part of the behavior genome.

    The behavior text may be a write-side template: when it declares
    ``:parameters`` and references ``${var}`` placeholders, the declaration
    is validated and the text is checked structurally (placeholders
    substituted to type stand-ins). A concrete (non-template) text must
    parse as EDN and pass the unified ``MemberSpec`` schema.

    The metadata is written with ``:trusted false`` and ``:author`` recording
    the authoring agent, so ``spawnAgent`` refuses it until a human approves
    it in the mirror. Lineage (issue ): ``parent`` links the proposal to
    the prototype it was derived from (trusted or staging) and the
    generation is derived server-side as the parent's + 1; ``proposal_note``
    records what this generation changes.

    Args:
        name: prototype directory name (charset-checked, must be unique
            across both the trusted library and the staging area)
        behavior_edn: raw EDN text of the unified behavior spec
        display_name: human-facing name (falls back to ``name``)
        description: human-facing description
        author_id: id of the agent that authored the spec (audit)
        parent: name of the prototype this one derives from (must exist in
            either area; None = a new root)
        proposal_note: free-text description of this generation's change
        lessons: lessons text (issue ), written as ``LESSONS.md``
            (None = the prototype carries no lessons file)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Metadata of the staged prototype (``trusted=False``).

    Raises:
        PrototypeError: invalid name, name conflict, unknown parent, or spec
            failed template / EDN / schema validation (nothing is written in
            any case)
    """
    p = tlon_paths or paths
    _validate_name(name)

    if (p.prototypes_dir() / name / BEHAVIOR_FILE).exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")
    staging_dir = p.prototypes_staging_dir() / name
    if staging_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the staging area")

    # Validate first: a broken spec must never reach disk.
    generation = _resolve_parent_generation(parent, p) if parent is not None else 1
    spec_text = _validate_behavior(behavior_edn)

    staging_dir.mkdir(parents=True)

    (staging_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")
    if lessons:
        (staging_dir / LESSONS_FILE).write_text(lessons, encoding="utf-8")

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name or name,
        Keyword("description"): description,
        Keyword("trusted"): False,
        Keyword("author"): author_id,
        Keyword("generation"): generation,
    }
    if parent is not None:
        metadata[Keyword("parent")] = Keyword(parent)
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    (staging_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info(
        "staging_prototype_written",
        name=name,
        author_id=author_id,
        parent=parent,
        generation=generation,
    )
    return _read_metadata(staging_dir)


def list_staging_prototypes(tlon_paths: TlonPaths | None = None) -> list[PrototypeInfo]:
    """List prototypes awaiting approval in ``~/.tlon/prototypes-staging/``."""
    return _list_from((tlon_paths or paths).prototypes_staging_dir())


def approve_prototype(name: str, tlon_paths: TlonPaths | None = None) -> PrototypeInfo:
    """Promote a staged prototype to the trusted library (human approval).

    Moves the directory to ``~/.tlon/prototypes/`` and rewrites its
    metadata with ``:trusted true``; the ``:author`` record is kept.

    Raises:
        PrototypeError: invalid name, nothing staged under that name, or the
            trusted library already has a prototype with that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    staging_dir = p.prototypes_staging_dir() / name
    if not _is_prototype_dir(staging_dir):
        raise PrototypeError(f"no staged prototype named '{name}'")
    target_dir = p.prototypes_dir() / name
    if target_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging_dir), str(target_dir))

    metadata_file = target_dir / METADATA_FILE
    data: dict[Any, Any] = {}
    if metadata_file.exists():
        parsed = loads(metadata_file.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            data = dict(parsed)
    data[Keyword("trusted")] = True
    data.setdefault(Keyword("name"), Keyword(name))
    data.setdefault(Keyword("display-name"), name)
    data.setdefault(Keyword("description"), "")
    metadata_file.write_text(dumps(data) + "\n", encoding="utf-8")

    logger.info("prototype_approved", name=name, author=str(data.get("author") or ""))
    return _read_metadata(target_dir)


def reject_prototype(name: str, tlon_paths: TlonPaths | None = None) -> None:
    """Delete a staged prototype (human rejection).

    Path-injection guard: the name must pass the charset check and resolve
    strictly inside the staging root.

    Raises:
        PrototypeError: invalid name or nothing staged under that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    staging_root = p.prototypes_staging_dir().resolve()
    target = (staging_root / name).resolve()
    if target.parent != staging_root:
        raise PrototypeError(f"prototype name '{name}' resolves outside the staging area")
    if not target.is_dir():
        raise PrototypeError(f"no staged prototype named '{name}'")
    shutil.rmtree(target)
    logger.info("prototype_rejected", name=name)


def get_prototype_detail(
    name: str, staging: bool = False, tlon_paths: TlonPaths | None = None
) -> tuple[PrototypeInfo, str, str | None, str | None]:
    """Metadata plus the full ``behavior.tlon`` text of one prototype.

    Args:
        name: prototype directory name
        staging: read from the staging area instead of the trusted library
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        ``(metadata, behavior_text, lessons, reviews)`` — ``lessons`` is the
        ``LESSONS.md`` text (issue ), None when the prototype carries
        no lessons file; ``reviews`` is the ``REVIEW.md`` text (issue ,
        heterogeneous review records), None when no review has landed.

    Raises:
        PrototypeError: invalid name or no prototype under that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    root = p.prototypes_staging_dir() if staging else p.prototypes_dir()
    proto_dir = root / name
    if not _is_prototype_dir(proto_dir):
        where = "staging area" if staging else "trusted library"
        raise PrototypeError(f"no prototype named '{name}' in the {where}")
    info = _read_metadata(proto_dir)
    behavior = (proto_dir / BEHAVIOR_FILE).read_text(encoding="utf-8")
    lessons_file = proto_dir / LESSONS_FILE
    lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else None
    reviews_file = proto_dir / REVIEWS_FILE
    reviews = reviews_file.read_text(encoding="utf-8") if reviews_file.exists() else None
    return info, behavior, lessons, reviews


def append_staging_review(
    name: str, entry_text: str, tlon_paths: TlonPaths | None = None
) -> None:
    """Append one peer-review entry to a staged prototype's ``REVIEW.md``.

    The review record is advisory (issue ): it never gates approval —
    it rides along with the directory when ``approve_prototype`` moves the
    prototype into the trusted library, so the review trail survives
    promotion. Multiple reviews accumulate; nothing overwrites.

    Args:
        name: staged prototype directory name
        entry_text: one formatted review entry (timestamp, reviewer id,
            reviewer model, verdict, rationale — assembled by the caller);
            normalized to end with a newline
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Raises:
        PrototypeError: invalid name or nothing staged under that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    staging_dir = p.prototypes_staging_dir() / name
    if not _is_prototype_dir(staging_dir):
        raise PrototypeError(f"no staged prototype named '{name}'")
    entry = entry_text if entry_text.endswith("\n") else entry_text + "\n"
    review_file = staging_dir / REVIEWS_FILE
    if review_file.exists():
        with review_file.open("a", encoding="utf-8") as f:
            f.write("\n" + entry)
    else:
        review_file.write_text("# 评审记录\n\n" + entry, encoding="utf-8")
    logger.info("staging_review_appended", name=name)


_MAX_LINEAGE_DEPTH = 32


def get_prototype_lineage(
    name: str, tlon_paths: TlonPaths | None = None
) -> list[dict[str, Any]]:
    """Walk the ``:parent`` chain up to the root (issue ).

    The lineage is **derived**, never stored separately: each generation is
    read from its own ``prototype.edn`` (trusted or staging area, whichever
    holds it), and the diff between adjacent generations is a plain
    ``difflib`` unified diff of the two ``behavior.tlon`` texts.

    A parent that was deleted or renamed breaks the chain: the walk stops
    there and the oldest reachable entry is marked ``broken``. Cycles and
    runaway chains are cut at ``_MAX_LINEAGE_DEPTH`` / a visited set, also
    marked ``broken``.

    Args:
        name: prototype directory name (either area)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Root-first list of generations, each
        ``{name, generation, author, proposal_note, trusted, parent,
        diff_from_parent, broken}``; ``diff_from_parent`` is None for the
        root entry.

    Raises:
        PrototypeError: invalid name or no prototype under that name in
            either area
    """
    p = tlon_paths or paths
    _validate_name(name)
    proto_dir = _find_prototype_dir(name, p)
    if proto_dir is None:
        raise PrototypeError(f"no prototype named '{name}'")

    chain: list[dict[str, Any]] = []  # youngest first while walking up
    visited: set[str] = set()
    current: Path | None = proto_dir
    while current is not None:
        info = _read_metadata(current)
        entry: dict[str, Any] = {
            "name": info.name,
            "generation": info.generation,
            "author": info.author,
            "proposal_note": info.proposal_note,
            "trusted": info.trusted,
            "parent": info.parent,
            "broken": False,
            "_behavior": (current / BEHAVIOR_FILE).read_text(encoding="utf-8"),
        }
        chain.append(entry)
        visited.add(info.name)
        if info.parent is None:
            break
        if info.parent in visited or len(chain) >= _MAX_LINEAGE_DEPTH:
            entry["broken"] = True
            break
        current = _find_prototype_dir(info.parent, p)
        if current is None:
            entry["broken"] = True
    chain.reverse()

    for i, entry in enumerate(chain):
        if i == 0:
            entry["diff_from_parent"] = None
        else:
            entry["diff_from_parent"] = "".join(
                difflib.unified_diff(
                    chain[i - 1]["_behavior"].splitlines(keepends=True),
                    entry["_behavior"].splitlines(keepends=True),
                    fromfile=f"{chain[i - 1]['name']}/{BEHAVIOR_FILE}",
                    tofile=f"{entry['name']}/{BEHAVIOR_FILE}",
                )
            )
    for entry in chain:
        del entry["_behavior"]
    return chain


def write_trusted_prototype(
    name: str,
    behavior_edn: str,
    display_name: str = "",
    description: str = "",
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    tlon_paths: TlonPaths | None = None,
) -> PrototypeInfo:
    """Write a human-authored prototype directly into the trusted library.

    Handwritten implies trusted (the same rule the metadata reader encodes):
    the metadata is written with ``:trusted true`` and no ``:author``.
    Validation happens **before** anything touches disk — the same
    chain as the staging gate. Lineage fields (issue ) work exactly as
    in the staging write path.

    Args:
        name: prototype directory name (charset-checked, must be unique
            across both the trusted library and the staging area)
        behavior_edn: raw EDN text of the unified behavior spec
        display_name: human-facing name (falls back to ``name``)
        description: human-facing description
        parent: name of the prototype this one derives from (must exist in
            either area; None = a new root)
        proposal_note: free-text description of this generation's change
        lessons: lessons text (issue ), written as ``LESSONS.md``
            (None = the prototype carries no lessons file)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Metadata of the created prototype (``trusted=True``).

    Raises:
        PrototypeError: invalid name, name conflict, unknown parent, or spec
            failed validation (nothing is written in any case)
    """
    p = tlon_paths or paths
    _validate_name(name)

    proto_dir = p.prototypes_dir() / name
    if (proto_dir / BEHAVIOR_FILE).exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")
    if (p.prototypes_staging_dir() / name).exists():
        raise PrototypeError(f"prototype '{name}' already exists in the staging area")

    generation = _resolve_parent_generation(parent, p) if parent is not None else 1
    spec_text = _validate_behavior(behavior_edn)

    proto_dir.mkdir(parents=True)
    (proto_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")
    if lessons:
        (proto_dir / LESSONS_FILE).write_text(lessons, encoding="utf-8")

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name or name,
        Keyword("description"): description,
        Keyword("trusted"): True,
        Keyword("generation"): generation,
    }
    if parent is not None:
        metadata[Keyword("parent")] = Keyword(parent)
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    (proto_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info("trusted_prototype_written", name=name)
    return _read_metadata(proto_dir)


def update_prototype(
    name: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    behavior_edn: str | None = None,
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    staging: bool = False,
    tlon_paths: TlonPaths | None = None,
) -> PrototypeInfo:
    """Update a prototype's metadata and/or rewrite its behavior file.

    A behavior change runs the full validation chain first — a broken spec
    raises and leaves the disk untouched. The metadata rewrite preserves
    the existing ``:trusted``/``:author`` fields (only the explicitly
    provided fields change). Lineage (issue ): providing ``parent``
    re-links the prototype and re-derives its ``:generation`` server-side;
    ``proposal_note`` records what a generation changes.

    Args:
        name: prototype directory name
        display_name: new display name (None = keep)
        description: new description (None = keep)
        behavior_edn: new raw EDN behavior spec (None = keep)
        parent: new lineage parent (None = keep; must exist in either area)
        proposal_note: new change note (None = keep)
        lessons: new lessons text, rewritten as ``LESSONS.md``
            (None = keep, issue )
        staging: update in the staging area instead of the trusted library
            (pre-approval editing of an LLM-authored prototype)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Metadata of the updated prototype.

    Raises:
        PrototypeError: invalid name, prototype not found, unknown parent,
            nothing to update, or the new behavior failed validation
            (nothing is written in the last cases)
    """
    p = tlon_paths or paths
    _validate_name(name)
    root = p.prototypes_staging_dir() if staging else p.prototypes_dir()
    proto_dir = root / name
    if not _is_prototype_dir(proto_dir):
        where = "staging area" if staging else "trusted library"
        raise PrototypeError(f"no prototype named '{name}' in the {where}")
    if (
        display_name is None
        and description is None
        and behavior_edn is None
        and parent is None
        and proposal_note is None
        and lessons is None
    ):
        raise PrototypeError(
            "nothing to update: provide display_name, description, behavior_edn, "
            "parent, proposal_note or lessons"
        )

    # Resolve everything that can fail before anything touches disk.
    parent_generation: int | None = None
    if parent is not None:
        parent_generation = _resolve_parent_generation(parent, p)
    spec_text = _validate_behavior(behavior_edn) if behavior_edn is not None else None

    if spec_text is not None:
        (proto_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")
    if lessons is not None:
        (proto_dir / LESSONS_FILE).write_text(lessons, encoding="utf-8")

    if (
        display_name is not None
        or description is not None
        or parent is not None
        or proposal_note is not None
    ):
        metadata_file = proto_dir / METADATA_FILE
        data: dict[Any, Any] = {}
        if metadata_file.exists():
            parsed = loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = dict(parsed)
        if display_name is not None:
            data[Keyword("display-name")] = display_name
        if description is not None:
            data[Keyword("description")] = description
        if parent is not None:
            data[Keyword("parent")] = Keyword(parent)
            data[Keyword("generation")] = parent_generation
        if proposal_note is not None:
            data[Keyword("proposal-note")] = proposal_note
        data.setdefault(Keyword("name"), Keyword(name))
        data.setdefault(Keyword("display-name"), name)
        data.setdefault(Keyword("description"), "")
        data.setdefault(Keyword("trusted"), not staging)
        metadata_file.write_text(dumps(data) + "\n", encoding="utf-8")

    logger.info(
        "prototype_updated",
        name=name,
        staging=staging,
        behavior_rewritten=spec_text is not None,
    )
    return _read_metadata(proto_dir)


def delete_prototype(name: str, tlon_paths: TlonPaths | None = None) -> None:
    """Delete a prototype from the trusted library.

    Deleting a staged prototype is the existing ``reject_prototype``; this
    is the trusted-library counterpart. Path-injection guard: the name
    must pass the charset check and resolve strictly inside the library
    root.

    Raises:
        PrototypeError: invalid name or no trusted prototype under that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    library_root = p.prototypes_dir().resolve()
    target = (library_root / name).resolve()
    if target.parent != library_root:
        raise PrototypeError(f"prototype name '{name}' resolves outside the trusted library")
    if not _is_prototype_dir(target):
        raise PrototypeError(f"no prototype named '{name}' in the trusted library")
    shutil.rmtree(target)
    logger.info("prototype_deleted", name=name)


def _validate_name(name: str) -> None:
    """Prototype names share the AgentConfig charset: alnum plus ``-``/``_``."""
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise PrototypeError(
            f"invalid prototype name {name!r}: only letters, digits, '-' and '_' allowed"
        )


# ==================== Near-kin rejection (issue ) ====================

NEAR_KIN_THRESHOLD = 0.9
"""Similarity cutoff for near-kin rejection — deliberately high: only
reskin-level duplicates (a proposal that is textually almost identical to
an existing prototype) are turned away, and only **across bloodlines**:
the declared parent is exempt (see ``find_near_kin``'s ``exclude``), so a
generational micro-tweak along its own lineage passes.

Measured anchor ( smoke case): a scribe-derived spec whose only change
is the reflect interval 15 → 30 scores 0.996 after normalization — caught
when compared against the population at large, released when the near-kin
is the proposal's own parent. Substantive rewrites land far below 0.9.
"""


def _normalize_behavior_text(text: str) -> str:
    """Lightweight normalization before similarity comparison.

    Drops EDN comment lines (``;``-prefixed) and collapses every whitespace
    run to a single space, so reformatting alone can neither dodge nor
    trigger the gate. The text is otherwise compared verbatim — no EDN
    parse, so write-side templates carrying ``${var}`` placeholders compare
    fine as plain text.
    """
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(";")]
    return " ".join(" ".join(lines).split())


def find_near_kin(
    behavior_text: str,
    exclude: str | None = None,
    *,
    threshold: float = NEAR_KIN_THRESHOLD,
    tlon_paths: TlonPaths | None = None,
) -> tuple[str, float] | None:
    """Find the most similar existing prototype across both areas.

    Compares ``behavior_text`` against every ``behavior.tlon`` in the
    trusted library and the staging area with
    ``difflib.SequenceMatcher.ratio`` on normalized text (pure stdlib text
    similarity — behavior specs are small canonical EDN texts, so an
    embedding model would buy nothing here).

    Args:
        behavior_text: raw behavior spec text of the proposal
        exclude: prototype name to skip — the proposal's declared parent.
            A child resembling its parent is the whole point of the
            generation channel (NEAT-style speciation protection:
            innovation is protected for a few generations inside its own
            bloodline); what the gate rejects is cross-bloodline
            convergence, not micro-evolution along a lineage.
        threshold: minimum ratio that counts as near-kin
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        ``(prototype_name, ratio)`` of the closest match when its ratio
        reaches ``threshold``, else None (also None for an empty library or
        an empty/blank proposal text). Unreadable prototypes are skipped —
        a broken file must never block a proposal.
    """
    p = tlon_paths or paths
    candidate = _normalize_behavior_text(behavior_text)
    if not candidate:
        return None
    best: tuple[str, float] | None = None
    for root in (p.prototypes_dir(), p.prototypes_staging_dir()):
        if not root.exists():
            continue
        for proto_dir in sorted(root.iterdir()):
            behavior_file = proto_dir / BEHAVIOR_FILE
            if not proto_dir.is_dir() or not behavior_file.exists():
                continue
            if proto_dir.name == exclude:
                continue
            try:
                existing = _normalize_behavior_text(
                    behavior_file.read_text(encoding="utf-8")
                )
            except OSError as e:
                logger.warning("near_kin_unreadable", path=str(behavior_file), error=str(e))
                continue
            if not existing:
                continue
            ratio = difflib.SequenceMatcher(None, candidate, existing).ratio()
            if best is None or ratio > best[1]:
                best = (proto_dir.name, ratio)
    if best is not None and best[1] >= threshold:
        return best
    return None
