"""``render``, with the response's status in the context as ``http_status``.

Use it in place of ``django.shortcuts.render`` for every page. fivebar's
tag (``templates/includes/_analytics.html``) writes ``http_status`` as
``data-status``, which Safari needs to count a 4xx or 5xx page as an error
rather than a pageview; Chrome, Edge and Firefox report it themselves
(https://fiveb.ar/docs/error-pages). The status is set once, here, so the
page and the response can't disagree.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render as django_render


def render(
    request: HttpRequest,
    template_name: str | list[str],
    context: dict[str, Any] | None = None,
    status: int = 200,
) -> HttpResponse:
    """``django.shortcuts.render``, telling the template its status."""
    return django_render(
        request, template_name, {**(context or {}), "http_status": status}, status=status
    )
