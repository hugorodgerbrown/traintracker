"""Where to send someone after they sign in."""

from django.conf import settings
from django.http import HttpRequest
from django.shortcuts import resolve_url
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request: HttpRequest, candidate: str | None) -> str:
    """Return ``candidate`` if it is a path on this site, else the default page.

    ``next`` arrives from a query string, a form or a stored sign-in request;
    all of them are attacker-shaped, so only same-site paths survive.
    """
    if (
        candidate
        and candidate.startswith("/")
        and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
    ):
        return candidate
    return resolve_url(settings.LOGIN_REDIRECT_URL)
