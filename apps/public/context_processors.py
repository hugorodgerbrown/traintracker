"""The site's header and footer on pages outside the public layout, such as the account page."""

import logging
from typing import Any

from django.http import HttpRequest

logger = logging.getLogger(__name__)


def site(request: HttpRequest) -> dict[str, Any]:
    """The footer's links and the current path, for ``public/_header.html`` and ``_footer.html``.

    The public pages set both themselves (``views.page_context``), which wins.
    """
    from apps.public.views import NAV, relative

    return {
        "nav": [(relative(p), text, p == request.path) for p, text in NAV],
        "current": request.path,
    }
