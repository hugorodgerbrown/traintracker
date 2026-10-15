"""Background tasks for accounts: the sign-in email.

Views enqueue; they never send mail themselves, so a slow mail server
can't hold a request (settings.TASKS chooses where the task runs: on a
thread of its own, apps.core.task_backends.ThreadBackend).

The task's arguments are the one place the email address goes, so it is
not stored: the thread backend keeps them in memory. A queue-backed
backend would write them to its queue until the email is sent; clear
them once it is, or the address is stored after all.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import send_mail
from django.tasks import task
from django.template.loader import render_to_string

from apps.pwa import conf


@task()
def send_sign_in_email(email: str, link: str, code: str) -> None:
    """Email ``email`` its sign-in link and code, as plain text and as simple HTML."""
    context = {
        "link": link,
        "code": code,
        "minutes": settings.SIGN_IN_MAX_AGE_SECONDS // 60,
        "site_name": conf.APP_NAME,
    }
    subject = render_to_string("accounts/emails/sign_in_subject.txt", context).strip()
    body = render_to_string("accounts/emails/sign_in.txt", context)
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
    send_mail(subject, body, None, [email], html_message=html)
