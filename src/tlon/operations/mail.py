"""One authoritative mail contract, projected to DSL and LLM tools."""

from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError
from tlon.organization.mail.member_mail import CHECK_INBOX_MAX_LIMIT, SEARCH_MAILS_MAX_LIMIT
from tlosure.builtins import Builtin


class MailPort(Protocol):
    async def send(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]: ...
    async def forward(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]: ...
    async def check_inbox(self, arguments: dict[str, Any]) -> dict[str, Any]: ...
    async def search(self, arguments: dict[str, Any]) -> dict[str, Any]: ...
    async def mark_read(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


_STRING = {"type": "string"}
_NULLABLE_STRING = {"type": ["string", "null"]}
_STRINGS = _array(_STRING)
_MAIL_PROPERTIES = {
    **dict.fromkeys(("id", "from_address", "content", "thread_id", "timestamp"), _STRING),
    **dict.fromkeys(("subject", "in_reply_to", "forward_of", "endorsing"), _NULLABLE_STRING),
    "to": _STRINGS,
    "cc": _STRINGS,
    "external": {"type": "boolean"},
}
_MAIL = _object(_MAIL_PROPERTIES)
_SEARCH_MAIL = _object({**_MAIL_PROPERTIES, "box": {"type": "string", "enum": ["inbox", "sent"]}})
_SUMMARY = _object(
    {
        **{
            key: _MAIL_PROPERTIES[key]
            for key in (
                "id",
                "from_address",
                "to",
                "cc",
                "subject",
                "timestamp",
                "thread_id",
                "external",
            )
        },
        "box": {"type": "string", "enum": ["inbox", "sent"]},
        "content_excerpt": _STRING,
    }
)
_RECEIPT = _object(
    {
        "status": {"type": "string", "enum": ["accepted"]},
        "mail_id": _STRING,
        "request_id": _STRING,
        "local_recipients": _STRINGS,
        "gateways": _array(
            _object(
                {"address": _STRING, "status": _STRING, "error": _NULLABLE_STRING},
                ["address", "status"],
            )
        ),
        "message": _STRING,
    }
)
_DELIVERY_INPUTS = {
    "request_id": {
        "type": "string",
        "minLength": 1,
        "description": "稳定投信标识；结果未知时用原值重试，同键不同内容会拒绝。",
    },
    "to": _STRINGS,
    "cc": _STRINGS,
}


def make_mail_operations(port: MailPort) -> tuple[Operation, ...]:
    """Bind one member's port to the shared mail definitions."""

    async def send(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await port.send(arguments, arguments.get("request_id", context.call_id))

    async def forward(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await port.forward(arguments, arguments.get("request_id", context.call_id))

    async def inbox(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await port.check_inbox(arguments)

    async def search(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await port.search(arguments)

    async def mark(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await port.mark_read(arguments)

    return (
        Operation(
            "mail-send",
            "发送邮件；accepted 表示本地已入箱，不代表已阅读或处理。网关只尝试投递。",
            _object(
                {
                    **_DELIVERY_INPUTS,
                    "content": {"type": "string", "minLength": 1},
                    "subject": _STRING,
                    "in_reply_to": _STRING,
                    "endorsing": _STRING,
                },
                ["to", "content"],
            ),
            _RECEIPT,
            send,
            effects=frozenset({"write"}),
        ),
        Operation(
            "mail-forward",
            "转发自己邮箱中的邮件；保留原文和归因，外部原信带不可信标记。",
            _object(
                {**_DELIVERY_INPUTS, "mail_id": _STRING, "comment": _STRING}, ["mail_id", "to"]
            ),
            _RECEIPT,
            forward,
            effects=frozenset({"write"}),
        ),
        Operation(
            "mail-check-inbox",
            "查看自己的未读邮件，按最旧在先返回完整正文；读取不标读。",
            _object(
                {"limit": {"type": "integer", "minimum": 1, "maximum": CHECK_INBOX_MAX_LIMIT}}, []
            ),
            _object(
                {
                    "mails": _array(_MAIL),
                    "count": {"type": "integer"},
                    "total_unread": {"type": "integer"},
                }
            ),
            inbox,
            effects=frozenset({"read"}),
        ),
        Operation(
            "mail-search",
            "检索自己邮箱；query 空格分词 AND 匹配并返回节选，mail_id 精确读取完整邮件。",
            _object(
                {
                    "query": _STRING,
                    "mail_id": _STRING,
                    "limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_MAILS_MAX_LIMIT},
                },
                [],
            ),
            _object(
                {
                    "mail": _SEARCH_MAIL,
                    "mails": _array(_SUMMARY),
                    "count": {"type": "integer"},
                    "query": _STRING,
                },
                [],
            ),
            search,
            effects=frozenset({"read"}),
        ),
        Operation(
            "mail-mark-read",
            "显式标读自己的收件箱；分别报告新标读、已读和不存在的 id。",
            _object({"mail_ids": {"type": "array", "items": _STRING, "minItems": 1}}),
            _object(dict.fromkeys(("marked", "already_read", "not_found"), _STRINGS)),
            mark,
            effects=frozenset({"write"}),
        ),
    )


class _UnboundMailPort:
    """Definition-only binding: no service lookup and no side effects."""

    async def send(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        raise OperationError("mail service is not bound")

    async def forward(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        raise OperationError("mail service is not bound")

    async def check_inbox(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OperationError("mail service is not bound")

    async def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OperationError("mail service is not bound")

    async def mark_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OperationError("mail service is not bound")


def mail_operation_definitions() -> tuple[Operation, ...]:
    """Inspect authoritative definitions without constructing runtime services."""
    return make_mail_operations(_UnboundMailPort())


def mail_builtin_definitions() -> dict[str, Builtin]:
    """Static DSL validation definitions; runtime binds a real DSLAdapter."""

    def unbound(_env: dict[str, Any], *args: Any) -> Any:
        raise OperationError("mail service is not bound")

    return {
        operation.name: Builtin(fn=unbound, pure=False)
        for operation in mail_operation_definitions()
    }
