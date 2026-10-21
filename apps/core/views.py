"""Site-wide views: the health checks. The error pages are in errors.py."""

import logging

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


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
