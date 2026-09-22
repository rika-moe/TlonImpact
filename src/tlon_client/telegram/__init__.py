"""Telegram client package for Tlon (design: docs/design/telegram.md).

A third human channel alongside the Operator TUI and the WebUI: push mail
to the phone and reply from the phone. One process = python-telegram-bot
(polling) + the official SDK TlonClient (TelegramAdapter, chat mode)
sharing a single asyncio event loop.

- ``bot.py``: the PTB application + message routing (requires the
  ``telegram`` optional extra: ``uv sync --extra telegram``)
- ``reply_map.py``: pure logic — telegram_message_id -> mail mapping,
  the user-id whitelist and /send command parsing (no PTB dependency,
  unit-testable in every environment)

The client adapter (TelegramAdapter) is NOT duplicated here: the one in
``tlon_client.sdk.adapter`` (PR1) is reused as-is.
"""
