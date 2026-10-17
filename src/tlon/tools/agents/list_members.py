"""Public member addressing directory shared with delivery and the DSL."""

import asyncio
from typing import Any

from tlon.tools.agents.ports import MemberDirectory
from tlon.tools.base import Tool, ToolResult


class ListMembersTool(Tool):
    """Discover correspondents without exposing private or social identity data."""

    def __init__(self, directory: MemberDirectory) -> None:
        super().__init__(
            name="listMembers",
            description=(
                "查询当前成员寻址黄页：id、名字、邮件地址。离线成员也可列出。"
                "黄页是公开寻址信息，可以引用和转告，用于发现通信对象；"
                "不认证职务、组织、能力或可信程度，也不授予启停权限。"
                "公开范围不包含他人的私人联系簿、人格文件、邮件正文或记忆。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        )
        self._directory = directory

    async def execute(self, **kwargs: Any) -> ToolResult:
        members = await asyncio.to_thread(self._directory.list_members)
        return ToolResult(
            success=True,
            data=[
                {"id": member["id"], "name": member["name"], "address": member["address"]}
                for member in members
            ],
            message=f"共 {len(members)} 个成员",
        )
