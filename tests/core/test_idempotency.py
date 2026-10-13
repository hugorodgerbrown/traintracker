"""Tests for apps.core.idempotency: each keyed write runs at most once."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory

from apps.core.idempotency import IdempotencyMiddleware
from apps.core.models import IdempotencyRecord
from tests.core import urls
from tests.factories import UserFactory

pytestmark = pytest.mark.urls("tests.core.urls")

URL = "/write/"


@pytest.fixture(autouse=True)
def _no_calls() -> None:
    """Each test starts with the endpoint never having run."""
    urls.CALLS.clear()


def post(client: Client, body: dict[str, Any], key: str | None = "key-1", url: str = URL) -> Any:
    """POST JSON to the test write endpoint, with an Idempotency-Key unless key is None."""
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(url, json.dumps(body), content_type="application/json", headers=headers)


def test_replay_returns_first_response_without_running_view_again(signed_in: Client) -> None:
    """A repeat of a keyed write answers from the record; the view ran once."""
    first = post(signed_in, {"text": "hello"})
    second = post(signed_in, {"text": "hello"})
    assert first.status_code == second.status_code == 201
    assert second.content == first.content
    assert second["Idempotent-Replayed"] == "true"
    assert len(urls.CALLS) == 1


def test_a_replay_keeps_the_headers_but_not_the_cookies(signed_in: Client) -> None:
    """Location (and HX-* and the like) come back on a replay; Set-Cookie never does."""
    first = post(signed_in, {"text": "hello"})
    second = post(signed_in, {"text": "hello"})
    assert second["Location"] == first["Location"] == "/write/1/"
    assert "seen" in first.cookies
    assert "seen" not in second.cookies


def test_same_key_different_query_string_is_a_conflict(signed_in: Client) -> None:
    """?id=1 and ?id=2 are different writes, though their path and body are the same."""
    post(signed_in, {}, url=f"{URL}?id=1")
    assert post(signed_in, {}, url=f"{URL}?id=2").status_code == 409
    assert len(urls.CALLS) == 1


def test_a_keyed_request_target_must_fit_the_record(signed_in: Client) -> None:
    """A path and query string too long to store is refused rather than half-checked."""
    response = post(signed_in, {}, url=f"{URL}?q={'x' * 2048}")
    assert response.status_code == 414
    assert not urls.CALLS


def test_no_key_passes_through(signed_in: Client) -> None:
    """Without a key every request runs, as for a plain form."""
    post(signed_in, {"text": "a"}, key=None)
    post(signed_in, {"text": "a"}, key=None)
    assert len(urls.CALLS) == 2
    assert not IdempotencyRecord.objects.exists()


def test_same_key_different_body_is_a_conflict(signed_in: Client) -> None:
    """A key reused for a different body gets 409, not the first response."""
    post(signed_in, {"text": "one"})
    assert post(signed_in, {"text": "two"}).status_code == 409
    assert len(urls.CALLS) == 1


def test_same_key_different_user_is_a_conflict(signed_in: Client, client: Client) -> None:
    """Another user replaying a key never sees the first user's response."""
    post(signed_in, {"text": "mine"})
    other = Client()
    other.force_login(UserFactory.create())
    assert post(other, {"text": "mine"}).status_code == 409


def test_in_flight_key_is_a_retryable_conflict(signed_in: Client, user: Any) -> None:
    """A repeat while the first request is still running gets 409 + Retry-After."""
    import hashlib
    from datetime import timedelta

    body = json.dumps({"text": "x"}).encode()
    IdempotencyRecord.objects.reserve(
        key="key-1",
        method="POST",
        path=URL,
        principal=str(user.pk),
        body_hash=hashlib.sha256(body).hexdigest(),
        ttl=timedelta(hours=1),
    )
    response = post(signed_in, {"text": "x"})
    assert response.status_code == 409
    assert response["Retry-After"] == "1"


@pytest.mark.parametrize("key", ["k" * 129, "ключ"])
def test_malformed_key_is_rejected(signed_in: Client, key: str) -> None:
    """Overlong or non-ASCII keys are refused before touching the database."""
    assert post(signed_in, {"text": "x"}, key=key).status_code == 400


@pytest.mark.django_db
def test_server_error_releases_the_key(rf: RequestFactory) -> None:
    """A 5xx isn't stored, so the retry runs the view again."""
    calls: list[int] = []

    def view(request: Any) -> HttpResponse:
        calls.append(1)
        return HttpResponse(status=503 if len(calls) == 1 else 201)

    middleware = IdempotencyMiddleware(view)
    for _ in range(2):
        middleware(
            rf.post(
                "/x", data=b"{}", content_type="application/json", headers={"Idempotency-Key": "k"}
            )
        )
    assert len(calls) == 2
    assert IdempotencyRecord.objects.get().response_status == 201


def _csrf_failure() -> HttpResponse:
    """A 403 as the CSRF failure view sends it."""
    response = HttpResponse(status=403)
    response["X-CSRF-Failure"] = "1"
    return response


@pytest.mark.django_db
@pytest.mark.parametrize(
    "first",
    [
        lambda: HttpResponse(status=401),
        _csrf_failure,
        lambda: HttpResponse(status=408),
        lambda: HttpResponse(status=425),
        lambda: HttpResponse(status=429),
    ],
    ids=["401", "csrf-403", "408", "425", "429"],
)
def test_paused_or_retried_responses_release_the_key(
    rf: RequestFactory, first: Callable[[], HttpResponse]
) -> None:
    """A reply the outbox retries isn't stored, so the retry reaches the view."""
    calls: list[int] = []

    def view(request: Any) -> HttpResponse:
        calls.append(1)
        return first() if len(calls) == 1 else HttpResponse(status=201)

    middleware = IdempotencyMiddleware(view)
    for _ in range(2):
        middleware(rf.post("/x", headers={"Idempotency-Key": "k"}))
    assert len(calls) == 2
    assert IdempotencyRecord.objects.get().response_status == 201


@pytest.mark.django_db
def test_a_plain_403_is_stored(rf: RequestFactory) -> None:
    """A permission refusal is final: the repeat replays it."""
    calls: list[int] = []

    def view(request: Any) -> HttpResponse:
        calls.append(1)
        return HttpResponse(status=403)

    middleware = IdempotencyMiddleware(view)
    for _ in range(2):
        middleware(rf.post("/x", headers={"Idempotency-Key": "k"}))
    assert len(calls) == 1


@pytest.mark.django_db
def test_exception_releases_the_key(rf: RequestFactory) -> None:
    """A view that raises leaves no reservation behind."""

    def view(request: Any) -> HttpResponse:
        raise RuntimeError

    middleware = IdempotencyMiddleware(view)
    with pytest.raises(RuntimeError):
        middleware(rf.post("/x", headers={"Idempotency-Key": "k"}))
    assert not IdempotencyRecord.objects.exists()


def test_get_is_never_recorded(signed_in: Client) -> None:
    """Reads pass straight through, key or not."""
    signed_in.get(URL, headers={"Idempotency-Key": "k"})
    assert not IdempotencyRecord.objects.exists()
