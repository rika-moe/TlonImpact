"""Messaging infrastructure for Tlon.

This module contains the member-side messaging infrastructure:
- Member: bus-citizen interface (identity + lifecycle + bus I/O)
- MemberInfo: read-model DTO for the UI / wire protocol
- HumanAdapter / AdapterManager: human users as bus citizens

Mail itself lives in ``tlon.organization.mail`` (model, mailbox,
delivery) — see docs/design/mail.md.
"""
