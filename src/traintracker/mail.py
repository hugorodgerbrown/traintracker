"""Sending the one-time sign-in code by email.

The sign-in page (oauth.py) only needs "send this code to this address", so the
provider sits behind a small protocol. Resend is called over its HTTP API: no
SMTP port has to be open on the host. The console mailer is for running the
server on a laptop; it writes the code to the log, so never use it on a host
whose logs other people can read.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

import httpx

from traintracker.errors import UpstreamError

if TYPE_CHECKING:
    from traintracker.config import Settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
CODE_MINUTES = 10


class MailError(UpstreamError):
    """The code could not be handed to the mail provider."""


class Mailer(Protocol):
    async def send_code(self, to: str, code: str, site: str) -> None:
        """Send `code` to `to`. `site` is the host the person is signing in to."""


def message(code: str, site: str) -> tuple[str, str]:
    """Subject and plain-text body. The subject carries the code so it can be
    read from a notification without opening the message."""
    subject = f"{code} is your {site} sign-in code"
    body = (
        f"Your sign-in code for {site} is {code}.\n\n"
        f"It expires in {CODE_MINUTES} minutes and can be used once.\n\n"
        "If you didn't ask for this code, ignore this message: nobody can sign in "
        "without it.\n"
    )
    return subject, body


class ResendMailer:
    def __init__(self, api_key: str, sender: str, timeout: float = 15) -> None:
        self.api_key = api_key
        self.sender = sender
        self.timeout = timeout

    async def send_code(self, to: str, code: str, site: str) -> None:
        subject, body = message(code, site)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                response = await http.post(
                    RESEND_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"from": self.sender, "to": [to], "subject": subject, "text": body},
                )
        except httpx.HTTPError as exc:
            raise MailError(f"Resend could not be reached: {type(exc).__name__}.") from exc
        if not response.is_success:
            # The body can repeat the recipient's address; keep it out of the logs.
            raise MailError(f"Resend refused the message (HTTP {response.status_code}).")


class ConsoleMailer:
    """Development only: the code goes to the log instead of a mailbox."""

    async def send_code(self, to: str, code: str, site: str) -> None:
        log.warning("Sign-in code for %s at %s: %s (MAIL_BACKEND=console)", to, site, code)


def build_mailer(settings: Settings) -> Mailer | None:
    """The mailer for these settings; None when email sign-in is off."""
    if not settings.email_sign_in:
        return None
    if settings.mail_backend == "console":
        return ConsoleMailer()
    assert settings.resend_api_key and settings.mail_from  # email_sign_in checked them
    return ResendMailer(settings.resend_api_key, settings.mail_from, settings.http_timeout)
