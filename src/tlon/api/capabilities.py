"""Capabilities facade：能力库（受信库 + staging 审批流，
docs/design/capabilities.md）。

收编 REST 层对 ``tlon.agents.capabilities`` 函数族的直接调用（受信库
列表/详情、staging 列表/创建、approve/reject 审批）。``tlon_paths``
在构造时注入，方法签名不再逐次传。

``CapabilityInfo``（方法返回类型）与 ``CapabilityError``（方法抛出）
作为本模块 API 面的一部分对 server 可见。
"""

from tlon.agents.capabilities.catalog import (
    get_capability_detail,
    list_trusted_capabilities,
)
from tlon.agents.capabilities.info import CapabilityInfo
from tlon.agents.capabilities.spec import CapabilityError
from tlon.agents.capabilities.staging import (
    approve_capability,
    list_staging_capabilities,
    reject_capability,
    write_staging_capability,
)
from tlon.base.paths import TlonPaths

__all__ = ["CapabilitiesFacade", "CapabilityError", "CapabilityInfo"]


class CapabilitiesFacade:
    """能力域北向 API。构造器注入 TlonPaths，薄透传。

    Example:
        >>> caps = CapabilitiesFacade(tlon_paths=paths)
        >>> caps.list_trusted()
        >>> caps.approve("thread-digest")
    """

    def __init__(self, *, tlon_paths: TlonPaths):
        """Initialize the facade.

        Args:
            tlon_paths: Path resolver (capability library + staging area)
        """
        self._paths = tlon_paths

    def list_trusted(self) -> list[CapabilityInfo]:
        """Trusted capability words of the library (loader-injected into
        every member's registry on next wake)."""
        return list_trusted_capabilities(self._paths)

    def list_staging(self) -> list[CapabilityInfo]:
        """Capabilities awaiting human approval."""
        return list_staging_capabilities(self._paths)

    def get_detail(
        self, name: str, *, staging: bool = False
    ) -> tuple[CapabilityInfo, str, str | None]:
        """Metadata + full capability.tlon artifact text (+ REVIEW.md text
        when present) of one capability.

        Raises:
            CapabilityError: invalid name or no capability under that name
        """
        return get_capability_detail(name, staging=staging, tlon_paths=self._paths)

    def create_staging(
        self,
        artifact: str,
        *,
        author: str = "human",
        proposal_note: str | None = None,
    ) -> CapabilityInfo:
        """Write a capability artifact into the staging area (untrusted;
        validation precedes any disk write).

        The artifact's ``:name`` is the capability's identity — the
        staging directory is named after it.

        Raises:
            CapabilityError: invalid artifact, name conflict, or validation
                failure (nothing is written in any case)
        """
        return write_staging_capability(
            artifact,
            author_id=author,
            proposal_note=proposal_note,
            tlon_paths=self._paths,
        )

    def approve(self, name: str) -> CapabilityInfo:
        """Promote a staged capability to the trusted library (members pick
        the word up on next wake, via the vocabulary bridge).

        Raises:
            CapabilityError: invalid name or nothing staged under that name
        """
        return approve_capability(name, self._paths)

    def reject(self, name: str) -> None:
        """Delete a staged capability (human rejection).

        Raises:
            CapabilityError: invalid name or nothing staged under that name
        """
        reject_capability(name, self._paths)
