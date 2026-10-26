"""Integration boundary for outbound email.

Phase 1 has no provider configured. Callers must check `is_configured` and report
that truthfully to the user instead of pretending a message was sent. A real
implementation (SMTP, SES, ...) can be added here without touching callers.
"""

from typing import Protocol


class EmailSender(Protocol):
    is_configured: bool

    def send(self, to: str, subject: str, body: str) -> None: ...


class NotConfiguredSender:
    is_configured = False

    def send(self, to: str, subject: str, body: str) -> None:
        raise RuntimeError("No email provider is configured.")


def get_email_sender() -> EmailSender:
    return NotConfiguredSender()
