"""Telegram command - run the Telegram bot client (docs/design/telegram.md).

The bot runs in-process (no subprocess): python-telegram-bot polling and
the SDK's TlonClient share one asyncio event loop. Requires the
``telegram`` optional extra (``uv sync --extra telegram``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from tlon.base.settings import get_settings

if TYPE_CHECKING:
    from argparse import Namespace

_CONFIG_HINT = """\
请在 ~/.tlon/.env 中配置：
    TELEGRAM_BOT_TOKEN=<@BotFather 颁发的 token>
    TELEGRAM_ALLOWED_USERS=<允许使用的 Telegram user_id，逗号分隔>
（先用手机给 bot 发条消息，再访问
 https://api.telegram.org/bot<token>/getUpdates 即可看到自己的 user_id）"""


async def run_telegram(args: Namespace) -> int:
    """Run the telegram command (Telegram bot client).

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    settings = get_settings()

    if not settings.telegram_bot_token:
        print("\n❌ 缺少 TELEGRAM_BOT_TOKEN，Telegram bot 无法启动。")
        print(_CONFIG_HINT)
        return 1

    from tlon.app.clients.telegram.reply_map import parse_allowed_users

    try:
        allowed_users = parse_allowed_users(settings.telegram_allowed_users)
    except ValueError as e:
        print(f"\n❌ TELEGRAM_ALLOWED_USERS 解析失败：{e}")
        print(_CONFIG_HINT)
        return 1
    if not allowed_users:
        print("\n❌ TELEGRAM_ALLOWED_USERS 为空：白名单外的消息一律忽略，bot 将无人可用。")
        print(_CONFIG_HINT)
        return 1

    try:
        from tlon.app.clients.telegram.bot import TelegramBot
    except ImportError:
        print("\n❌ 未安装 python-telegram-bot（telegram extra），请先运行：")
        print("   uv sync --extra telegram")
        return 1

    username = getattr(args, "username", None) or os.getenv("TLON_USERNAME") or "guest"
    base_url = f"http://{args.host}:{args.rest_port}"

    bot = TelegramBot(
        token=settings.telegram_bot_token,
        allowed_users=allowed_users,
        username=username,
        base_url=base_url,
    )
    print(f"\n📨 Telegram bot 启动：身份 human:{username}，服务器 {base_url}")
    print(f"   白名单用户：{allowed_users}")
    await bot.run()
    return 0
