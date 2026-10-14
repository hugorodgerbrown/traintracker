"""Views for signing in and out, and the account page.

Public (outside the installed app's /app/ scope):
  /signin/                    email form, and the passkey button
  /signin/code/               "check your email", and the code form
  /signin/link/<token>/       a Continue button; the POST signs in
  /signin/passkey/options/    POST: WebAuthn challenge (JSON)
  /signin/passkey/            POST: verify and sign in (JSON)
  /signout/                   POST

In the app:
  /app/account/                          passkeys
  /app/account/passkeys/options/         POST: registration challenge (JSON)
  /app/account/passkeys/                 POST: verify and save (JSON)
  /app/account/passkeys/<uuid>/delete/   POST

The link page asks for a click rather than signing in on GET, because mail
scanners open links to check them, and would use the link up.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts import passkeys, sign_in
from apps.accounts.forms import CodeForm, EmailForm
from apps.accounts.models import Passkey, SignInRequest
from apps.accounts.tasks import send_sign_in_email
from apps.core import analytics
from apps.core.decorators import login_required_json, signed_in_user
from apps.core.ratelimit import client_ip, over_limit
from apps.core.redirects import safe_next

logger = logging.getLogger(__name__)

# The session remembers which request this browser started, so the code
# only works here.
SESSION_REQUEST = "accounts.sign_in_request"
# The address with its name hidden (sign_in.mask_email): never the address itself.
SESSION_EMAIL_HINT = "accounts.sign_in_email_hint"
_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _minutes() -> int:
    """How long a sign-in email works, for the pages that say so."""
    return int(settings.SIGN_IN_MAX_AGE_SECONDS // 60)


def _complete_sign_in(request: HttpRequest, sign_in_request: SignInRequest) -> HttpResponse:
    """Sign in the account a redeemed request belongs to, and send it on."""
    user = sign_in.user_for(sign_in_request)
    if user is None:
        return render(request, "accounts/account_disabled.html", status=403)
    # No sign-in before this one: the account was made just now (or by
    # createsuperuser and never used), which fivebar counts as a sign-up.
    first = user.last_login is None
    login(request, user, backend=_BACKEND)
    request.session.pop(SESSION_REQUEST, None)
    request.session.pop(SESSION_EMAIL_HINT, None)
    url = safe_next(request, sign_in_request.next_url)
    return redirect(analytics.mark(url, analytics.SIGNED_UP) if first else url)


@never_cache
@require_http_methods(["GET", "POST"])
def sign_in_view(request: HttpRequest) -> HttpResponse:
    """Ask for an email address and send it a sign-in link and code."""
    if request.user.is_authenticated and request.method == "GET":
        return redirect(safe_next(request, request.GET.get("next")))

    form = EmailForm(request.POST or None, initial={"next": request.GET.get("next", "")})
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if over_limit("sign-in-ip", client_ip(request), limit=10, window_seconds=600) or (
            over_limit("sign-in-email", email, limit=5, window_seconds=3600)
        ):
            form.add_error(None, "Too many sign-in emails. Wait a few minutes and try again.")
            return render(request, "accounts/sign_in.html", {"form": form}, status=429)

        next_url = safe_next(request, form.cleaned_data["next"])
        sign_in_request, token, code = sign_in.issue(email, next_url)
        link = settings.SITE_URL + reverse("accounts:sign_in_link", args=[token])
        send_sign_in_email.enqueue(email=email, link=link, code=code)
        request.session[SESSION_REQUEST] = str(sign_in_request.uuid)
        request.session[SESSION_EMAIL_HINT] = sign_in.mask_email(email)
        return redirect("accounts:sign_in_code")

    status = 400 if request.method == "POST" else 200
    return render(request, "accounts/sign_in.html", {"form": form}, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def sign_in_code(request: HttpRequest) -> HttpResponse:
    """Tell the user to check their email, and take the code from it."""
    request_uuid = request.session.get(SESSION_REQUEST)
    if not request_uuid:
        return redirect("accounts:sign_in")

    form = CodeForm(request.POST or None)
    status = 200
    if request.method == "POST":
        status = 400
        if over_limit("sign-in-code-ip", client_ip(request), limit=20, window_seconds=600):
            form.add_error(None, "Too many attempts. Wait a few minutes, or use the link.")
            status = 429
        elif form.is_valid():
            redeemed = sign_in.redeem_code(request_uuid, form.cleaned_data["code"])
            if redeemed is not None:
                return _complete_sign_in(request, redeemed)
            form.add_error(
                "code", "That code didn't work. Check the newest email, or use its link."
            )
    context = {
        "form": form,
        "email_hint": request.session.get(SESSION_EMAIL_HINT, ""),
        "minutes": _minutes(),
    }
    return render(request, "accounts/sign_in_code.html", context, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def sign_in_link(request: HttpRequest, token: str) -> HttpResponse:
    """Show a Continue button for an emailed link; the POST signs in."""
    if request.method == "GET":
        pending = sign_in.find_by_token(token)
        if pending is None:
            return render(
                request, "accounts/link_invalid.html", {"minutes": _minutes()}, status=410
            )
        return render(request, "accounts/sign_in_link.html")

    redeemed = sign_in.redeem_token(token)
    if redeemed is None:
        return render(request, "accounts/link_invalid.html", {"minutes": _minutes()}, status=410)
    return _complete_sign_in(request, redeemed)


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    """Sign out, and tell the browser to drop its HTTP cache of signed-in pages.

    pwa.js clears the service worker's page cache before the form posts.
    """
    logout(request)
    response = redirect(settings.LOGOUT_REDIRECT_URL)
    response["Clear-Site-Data"] = '"cache"'
    return response


def admin_login(request: HttpRequest) -> HttpResponse:
    """Send the admin's password form to the normal sign-in.

    Staff sign in like everyone else; a signed-in user who isn't staff is
    refused here rather than sent round in a loop.
    """
    if request.user.is_authenticated:
        raise PermissionDenied
    query = urlencode({"next": request.GET.get("next", reverse("admin:index"))})
    return redirect(f"{reverse('accounts:sign_in')}?{query}")


# ---------- passkey sign-in ----------


def _json_body(request: HttpRequest) -> dict[str, Any] | None:
    """Return the request's JSON object, or None if it isn't one."""
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@require_POST
def passkey_sign_in_options(request: HttpRequest) -> JsonResponse:
    """Return a challenge for ``navigator.credentials.get()``."""
    if over_limit("passkey-ip", client_ip(request), limit=30, window_seconds=600):
        return JsonResponse({"error": "rate_limited"}, status=429)
    return JsonResponse(passkeys.sign_in_options(request.session))


@require_POST
def passkey_sign_in(request: HttpRequest) -> JsonResponse:
    """Verify a passkey and sign its owner in.

    The body is ``{"credential": <the credential's JSON>, "next": <path>}``.
    An unknown passkey answers 404 with its id, so the page can tell the
    browser to forget it (``PublicKeyCredential.signalUnknownCredential``).
    """
    data = _json_body(request)
    if data is None or not isinstance(data.get("credential"), dict):
        return JsonResponse({"error": "bad_request"}, status=400)
    try:
        user = passkeys.authenticate(json.dumps(data["credential"]), request.session)
    except passkeys.UnknownPasskeyError as exc:
        return JsonResponse(
            {"error": "unknown_passkey", "credentialId": exc.credential_id}, status=404
        )
    except passkeys.PasskeyError as exc:
        logger.info("passkeys.sign_in_failed %s", exc)
        return JsonResponse({"error": "not_verified"}, status=400)
    if not user.is_active:
        return JsonResponse({"error": "account_disabled"}, status=403)
    login(request, user, backend=_BACKEND)
    raw_next = data.get("next")
    return JsonResponse(
        {"next": safe_next(request, raw_next if isinstance(raw_next, str) else None)}
    )


# ---------- the account page ----------


@require_GET
@login_required
def account(request: HttpRequest) -> HttpResponse:
    """The signed-in user's passkeys (there is no email address to show)."""
    user = signed_in_user(request)
    return render(
        request,
        "accounts/account.html",
        {"passkeys": Passkey.objects.for_user(user)},
    )


@require_POST
@login_required_json
def passkey_register_options(request: HttpRequest) -> JsonResponse:
    """Return a challenge for ``navigator.credentials.create()``."""
    return JsonResponse(passkeys.registration_options(signed_in_user(request), request.session))


@require_POST
@login_required_json
def passkey_register(request: HttpRequest) -> JsonResponse:
    """Verify a new passkey and save it. The body is the credential's JSON."""
    if over_limit("passkey-ip", client_ip(request), limit=30, window_seconds=600):
        return JsonResponse({"error": "rate_limited"}, status=429)
    if _json_body(request) is None:
        return JsonResponse({"error": "bad_request"}, status=400)
    name = passkeys.default_name(request.headers.get("User-Agent", ""))
    try:
        passkey = passkeys.register(
            request.body.decode(), request.session, signed_in_user(request), name
        )
    except passkeys.PasskeyError as exc:
        logger.info("passkeys.register_failed %s", exc)
        return JsonResponse({"error": "not_verified"}, status=400)
    return JsonResponse({"uuid": str(passkey.uuid), "name": passkey.name}, status=201)


@require_POST
@login_required
def passkey_delete(request: HttpRequest, uuid: str) -> HttpResponse:
    """Remove one of the signed-in user's passkeys."""
    passkey = get_object_or_404(Passkey, uuid=uuid, user=signed_in_user(request))
    passkey.delete()
    return redirect("accounts:account")
