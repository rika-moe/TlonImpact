"""Prototypes facade：原型库（trusted library + staging 审批流）。

收编 REST 层对 ``tlon.agents.prototypes`` 函数族的直接调用（托管库
CRUD、staging 列表/编辑、approve/reject 审批）。``tlon_paths`` 在构造
时注入，方法签名不再逐次传。

``PrototypeInfo``（方法返回类型）与 ``PrototypeError``（方法抛出）
作为本模块 API 面的一部分对 server 可见。
"""

from typing import Any

from tlon.agents.prototypes.catalog import get_prototype_detail, list_prototypes
from tlon.agents.prototypes.info import PrototypeInfo
from tlon.agents.prototypes.lineage import get_prototype_lineage
from tlon.agents.prototypes.management import (
    delete_prototype,
    update_prototype,
    write_trusted_prototype,
)
from tlon.agents.prototypes.staging import (
    approve_prototype,
    list_staging_prototypes,
    reject_prototype,
)
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError

__all__ = ["PrototypeError", "PrototypeInfo", "PrototypesFacade"]


class PrototypesFacade:
    """原型域北向 API。构造器注入 TlonPaths，薄透传。

    Example:
        >>> protos = PrototypesFacade(tlon_paths=paths)
        >>> protos.list_trusted()
        >>> protos.approve("llm-idea")
    """

    def __init__(self, *, tlon_paths: TlonPaths):
        """Initialize the facade.

        Args:
            tlon_paths: Path resolver (prototype library + staging area)
        """
        self._paths = tlon_paths

    def list_staging(self) -> list[PrototypeInfo]:
        """LLM-authored prototypes awaiting human approval."""
        return list_staging_prototypes(self._paths)

    def list_trusted(self) -> list[PrototypeInfo]:
        """Trusted prototypes a member can be created from."""
        return list_prototypes(self._paths)

    def get_detail(
        self, name: str, *, staging: bool = False
    ) -> tuple[PrototypeInfo, str, str | None, str | None]:
        """Metadata + full behavior text (+ LESSONS.md / REVIEW.md texts
        when present) of one prototype.

        Raises:
            PrototypeError: invalid name or no prototype under that name
        """
        return get_prototype_detail(
            name, staging=staging, tlon_paths=self._paths
        )

    def get_lineage(self, name: str) -> list[dict[str, Any]]:
        """The derived parent chain with per-generation diffs ()."""
        return get_prototype_lineage(name, tlon_paths=self._paths)

    def create(
        self,
        name: str,
        behavior: str,
        *,
        display_name: str = "",
        description: str = "",
        parent: str | None = None,
        proposal_note: str | None = None,
        lessons: str | None = None,
    ) -> PrototypeInfo:
        """Write a human-authored prototype into the trusted library
        (handwritten implies trusted; validation precedes any disk write).

        Raises:
            PrototypeError: invalid name or a spec that fails validation
        """
        return write_trusted_prototype(
            name,
            behavior,
            display_name=display_name,
            description=description,
            parent=parent,
            proposal_note=proposal_note,
            lessons=lessons,
            tlon_paths=self._paths,
        )

    def update(
        self,
        name: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        behavior: str | None = None,
        parent: str | None = None,
        proposal_note: str | None = None,
        lessons: str | None = None,
        staging: bool = False,
    ) -> PrototypeInfo:
        """Update metadata and/or behavior of a prototype (a behavior
        change reruns validation; a failure leaves the disk untouched).

        Raises:
            PrototypeError: invalid name or a behavior that fails validation
        """
        return update_prototype(
            name,
            display_name=display_name,
            description=description,
            behavior_edn=behavior,
            parent=parent,
            proposal_note=proposal_note,
            lessons=lessons,
            staging=staging,
            tlon_paths=self._paths,
        )

    def delete(self, name: str) -> None:
        """Remove a prototype from the trusted library (staging removal is
        ``reject``).

        Raises:
            PrototypeError: invalid name or no trusted prototype
        """
        delete_prototype(name, self._paths)

    def approve(self, name: str) -> PrototypeInfo:
        """Promote a staged prototype to the trusted library.

        Raises:
            PrototypeError: invalid name or nothing staged under that name
        """
        return approve_prototype(name, self._paths)

    def reject(self, name: str) -> None:
        """Delete a staged prototype (human rejection).

        Raises:
            PrototypeError: invalid name or nothing staged under that name
        """
        reject_prototype(name, self._paths)
