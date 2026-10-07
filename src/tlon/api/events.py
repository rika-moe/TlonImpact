"""Events facade：runtime 事件的订阅端口 + GitHub webhook ingress。

收编 server 侧对事件系统与适配器生命周期的直接触点：

- 订阅端口：NotificationService 原来直接订阅进程内 EventBus，改经
  :meth:`EventsFacade.subscribe_all` / :meth:`EventsFacade.unsubscribe`
- 人类在场（presence）：SSE 会话挂接/摘下 HumanAdapter
  （``attach_human`` / ``detach_human``）与在线人类名册
  （``list_online_humans`` / ``get_online_human``，返回 ``MemberInfo``
  —— 该 DTO 是 facade 方法的返回类型，作为本模块 API 面的一部分对
  server 可见）
- GitHub ingress：``receive_github_event`` 把验签后的 webhook 事件
  交给 runtime 的 GitHubAdapter，返回落信的 mail id
"""

from typing import Any

from tlon.base.device import EventHandler
from tlon.base.events.event import EventType
from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging.github_adapter import GitHubAdapter
from tlon.organization.messaging.human_adapter import AdapterManager
from tlon.organization.messaging.member_info import MemberInfo

__all__ = ["EventsFacade", "MemberInfo"]


class EventsFacade:
    """事件域北向 API。构造器注入 EventBus / AdapterManager / 可选的
    GitHubAdapter，薄透传。

    Example:
        >>> events = EventsFacade(event_bus=bus, adapter_manager=am)
        >>> token = events.subscribe_all(handler)
        >>> events.unsubscribe(token)
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        adapter_manager: AdapterManager | None = None,
        github_adapter: GitHubAdapter | None = None,
    ):
        """Initialize the facade.

        Args:
            event_bus: The runtime event bus (subscription port)
            adapter_manager: Human adapter lifecycle (presence + SSE sinks)
            github_adapter: GitHub webhook ingress; None when no webhook
                secret is configured (the ingress is then disabled)
        """
        self._event_bus = event_bus
        self._adapter_manager = adapter_manager
        self._github_adapter = github_adapter
        # subscription token -> EventBus subscription ids
        self._subscriptions: dict[str, list[str]] = {}

    # ---------- 订阅端口 ----------

    def subscribe_all(self, handler: EventHandler) -> str:
        """Subscribe a handler to every event type.

        Args:
            handler: Callback (sync or async) receiving each event

        Returns:
            A subscription token for ``unsubscribe``
        """
        token = f"events-sub-{len(self._subscriptions)}"
        self._subscriptions[token] = [
            self._event_bus.subscribe(event_type, handler) for event_type in EventType
        ]
        return token

    def unsubscribe(self, token: str) -> None:
        """Cancel a subscription previously made with ``subscribe_all``."""
        for subscription_id in self._subscriptions.pop(token, []):
            self._event_bus.unsubscribe(subscription_id)

    # ---------- 人类在场（presence） ----------

    async def attach_human(self, username: str, session_id: str, sink: EventHandler) -> None:
        """Attach a client session to the human's adapter (creating it on
        first contact — the persistent mailbox included)."""
        assert self._adapter_manager is not None, "human presence not wired"
        await self._adapter_manager.attach(username, session_id, sink)

    async def detach_human(self, session_id: str) -> None:
        """Detach a session; the adapter sleeps when its last session leaves."""
        if self._adapter_manager is not None:
            await self._adapter_manager.detach(session_id)

    def list_online_humans(self) -> list[MemberInfo]:
        """Online human members (one per live adapter)."""
        if self._adapter_manager is None:
            return []
        return [
            MemberInfo(
                id=adapter.member_id,
                name=adapter.username,
                type="human",
                metadata={"session_count": adapter.session_count},
            )
            for adapter in self._adapter_manager.list_online()
        ]

    def get_online_human(self, username: str) -> MemberInfo | None:
        """One online human member by username, or None when offline."""
        if self._adapter_manager is None:
            return None
        adapter = self._adapter_manager.get_adapter(username)
        if adapter is None:
            return None
        return MemberInfo(
            id=adapter.member_id,
            name=adapter.username,
            type="human",
            metadata={"session_count": adapter.session_count},
        )

    # ---------- GitHub webhook ingress ----------

    @property
    def github_enabled(self) -> bool:
        """Whether the GitHub webhook ingress is configured."""
        return self._github_adapter is not None

    async def receive_github_event(self, event_type: str, payload: dict[str, Any]) -> str | None:
        """Hand a verified GitHub webhook delivery to the runtime.

        Returns:
            The id of the incoming mail the event was mapped to, or None
            when the adapter skipped it (unsupported event/action)
        """
        assert self._github_adapter is not None, "github ingress not configured"
        mail = await self._github_adapter.receive(event_type, payload)
        return mail.id if mail is not None else None
