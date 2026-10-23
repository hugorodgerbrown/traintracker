"""Site-wide views: the health checks and security.txt. The error pages are in errors.py."""

import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import cache_control, never_cache

logger = logging.getLogger(__name__)

# How far ahead security.txt's Expires field reads (RFC 9116 asks for less
# than a year). It rolls with the date: docs/decisions/security-txt-expires-rolls.md.
SECURITY_TXT_LIFETIME = timedelta(days=180)


@never_cache
def livez(request: HttpRequest) -> HttpResponse:
    """Render's health check: 200 while the process can answer at all.

    It does no work, and must never read ``request.user`` (a session
    lookup is a database query). A database outage then shows on
    /healthz without Render restarting healthy web processes over it.
    """
    return HttpResponse("ok", content_type="text/plain")


@never_cache
def healthz(request: HttpRequest) -> HttpResponse:
    """200 when the process is up and the database answers a read; 503 otherwise.

    The error is logged, never returned: a driver's message can carry the
    database host and role.
    """
    try:
        get_user_model().objects.exists()
    except DatabaseError:
        logger.exception("healthz: database read failed")
        return HttpResponse("error", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


@cache_control(public=True, max_age=60 * 60 * 24)
def security_txt(request: HttpRequest) -> HttpResponse:
    """RFC 9116's /.well-known/security.txt: who to tell about a vulnerability.

    Built per request rather than kept as a static file, so Expires is
    always in the future and Canonical names the site's own address.
    """
    expires = (datetime.now(UTC) + SECURITY_TXT_LIFETIME).replace(microsecond=0)
    lines = [
        f"Contact: {settings.SECURITY_CONTACT}",
        f"Expires: {expires.isoformat().replace('+00:00', 'Z')}",
        "Preferred-Languages: en",
        f"Canonical: {settings.SITE_URL}/.well-known/security.txt",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
