"""View decorators shared across apps."""

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse

View = Callable[..., HttpResponse]


def require_htmx(view: View) -> View:
    """Reject a request that htmx didn't send, with a 400.

    Every fragment (``partials/``) view uses this: a fragment loaded as a
    page is always a bug.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.headers.get("HX-Request") != "true":
            return HttpResponseBadRequest("This endpoint only answers htmx requests.")
        return view(request, *args, **kwargs)

    return wrapper


def login_required_json(view: View) -> View:
    """Answer a signed-out request with 401 JSON instead of a redirect.

    For endpoints the offline outbox posts to: a redirect to the sign-in
    page would look like success to fetch(), and the write would be lost.
    The outbox keeps the write and waits on a 401 until the user signs in.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return JsonResponse({"error": "signed_out"}, status=401)
        return view(request, *args, **kwargs)

    return wrapper


def signed_in_user(request: HttpRequest) -> AbstractBaseUser:
    """Return the signed-in user, for views already behind a login decorator.

    Narrows ``request.user`` for the type checker, and fails closed if a
    view ever loses its decorator.
    """
    user = request.user
    if not isinstance(user, AbstractBaseUser) or not user.is_authenticated:
        raise PermissionDenied
    return user
