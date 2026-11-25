"""Background tasks for accounts: the sign-in email.

Views enqueue; they never send mail themselves, so a slow mail server
can't hold a request (settings.TASKS chooses where the task runs: on a
thread of its own, apps.core.task_backends.ThreadBackend).

The address is only this task's argument, so with the thread backend it
stays in memory. A queue-backed backend would store it until the email is
sent, and the privacy notice would have to say so.

The email is written as a one-time verification code, with the link as a
shortcut, so mail apps such as Gmail recognise it and offer to copy the code
(DESIGN.md, "The sign-in email").
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.tasks import task
from django.template.loader import render_to_string

from apps.pwa import conf


def otp_token_header(code: str) -> str:
    """Return the ``OTP-Token`` header value for ``code`` (draft-goto-otp-token).

    An Internet-Draft, not a standard: it names the code and the origin that
    asked for it, so a mail client could hand the code only to this site.
    """
    url = urlsplit(settings.SITE_URL)
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    port = f":{url.port}" if url.port and url.port != default_port else ""
    return f'"{code}"; origin="{url.scheme}://{url.hostname}{port}"'


@task()
def send_sign_in_email(email: str, link: str, code: str) -> None:
    """Email ``email`` its sign-in code and link, as plain text and HTML."""
    context = {
        "link": link,
        "code": code,
        "minutes": settings.SIGN_IN_MAX_AGE_SECONDS // 60,
        "site_name": conf.APP_NAME,
        "brand_colour": conf.THEME_COLOUR,
    }
    subject = render_to_string("accounts/emails/sign_in_subject.txt", context).strip()
    # The HTML link is written as http(s)://host/path in the template, so it
    # can only ever be a web address (a bare {{ link }} in href is what the
    # SAST rules refuse, as it could carry a javascript: URL).
    parts = urlsplit(link)
    html = render_to_string(
        "accounts/emails/sign_in.html",
        context
        | {
            "link_secure": parts.scheme == "https",
            "link_host": parts.netloc,
            "link_path": parts.path.removeprefix("/"),
        },
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string("accounts/emails/sign_in.txt", context),
        to=[email],
        headers={"OTP-Token": otp_token_header(code)},
    )
    message.attach_alternative(html, "text/html")
    message.send()
