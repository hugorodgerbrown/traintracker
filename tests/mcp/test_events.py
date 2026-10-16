"""Tests for apps.mcp.events: MCP tool calls counted in fivebar as server events."""

import contextlib
import inspect
import json
import threading
import urllib.error
from typing import Any
from unittest import mock

import pytest

from apps.core.analytics import counts_mcp
from apps.mcp import events, tools, views
from apps.public.pages import page_text
from tests.factories import UserFactory

DOMAIN = "example.ultraniche.ai"
TOKEN = "fb_send_only"


class InlinePool:
    """Stands in for the thread pool: runs each send at once, so a test can see it."""

    def submit(self, func: Any, *args: Any) -> None:
        """Run ``func`` now."""
        func(*args)


@pytest.fixture
def sent(monkeypatch: Any) -> list[dict[str, Any]]:
    """Capture each request fivebar would receive, as {"url", "headers", "body"}."""
    captured: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float) -> Any:
        captured.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": json.loads(request.data),
                "timeout": timeout,
            }
        )
        response = mock.MagicMock()
        response.__enter__.return_value.status = 202
        return response

    monkeypatch.setattr(events, "_pool", InlinePool())
    monkeypatch.setattr(events.urllib.request, "urlopen", urlopen)
    return captured


@pytest.fixture(autouse=True)
def answers(monkeypatch: Any) -> None:
    """Answer every train tool at once: these tests are about the event, not the trains.

    A call with ``"station": "nowhere"`` fails as a mistake the model can read.
    """

    def run(user: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("station") == "nowhere":
            raise tools.ToolError("No station matches 'nowhere'.")
        return {"services": []}

    monkeypatch.setattr(tools, "_run_train_tool", run)


@pytest.fixture
def on(settings: Any) -> None:
    """Turn fivebar on: a domain and a send-only token."""
    settings.FIVEBAR_DOMAIN = DOMAIN
    settings.FIVEBAR_TOKEN = TOKEN


def call(user: Any, name: str, arguments: dict[str, Any], client: str = "claude") -> Any:
    """Call a tool through the dispatcher, as the endpoint does."""
    return views._dispatch(user, "tools/call", {"name": name, "arguments": arguments}, client)


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
def test_a_tool_call_sends_one_event(sent: list[dict[str, Any]]) -> None:
    """The event names the tool, the app and a hashed caller, and nothing the person wrote."""
    user = UserFactory.create()
    call(user, "live_departures", {"station": "Liverpool Street private"})
    [request] = sent
    assert request["url"] == "https://fiveb.ar/api/events"
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request["timeout"] == events.TIMEOUT
    assert request["body"] == {
        "site": DOMAIN,
        "name": "/mcp/live_departures",
        "id": events.caller_id(user),
        "client": "claude",
        "props": {"outcome": "ok"},
    }
    assert "private" not in json.dumps(request["body"])


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
def test_the_caller_id_is_stable_per_user_and_not_the_id() -> None:
    """One user, one caller; two users, two; and no uuid or pk in it."""
    alice, bob = UserFactory.create(), UserFactory.create()
    assert events.caller_id(alice) == events.caller_id(alice)
    assert events.caller_id(alice) != events.caller_id(bob)
    assert str(alice.pk) != events.caller_id(alice)
    assert str(alice.uuid) not in events.caller_id(alice)


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
def test_a_tool_error_is_counted_as_an_error(sent: list[dict[str, Any]]) -> None:
    """A ToolError still answers, and counts with outcome "error"."""
    result = call(UserFactory.create(), "find_station", {"station": "nowhere"}, client="")
    assert result["isError"]
    [request] = sent
    assert request["body"]["props"] == {"outcome": "error"}
    assert "client" not in request["body"]


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/call", {"name": "no_such_tool"}),
        ("tools/call", {"name": "find_station", "arguments": "not an object"}),
        ("tools/call", {"name": "live_departures", "arguments": []}),
        ("tools/list", {}),
        ("initialize", {}),
    ],
)
def test_only_calls_to_a_listed_tool_are_counted(
    sent: list[dict[str, Any]], method: str, params: dict[str, Any]
) -> None:
    """An unknown tool, bad arguments or another method send nothing."""
    with contextlib.suppress(views.RpcError):
        views._dispatch(UserFactory.create(), method, params, "claude")
    assert sent == []


@pytest.mark.django_db
@pytest.mark.parametrize(("domain", "token"), [("", ""), (DOMAIN, ""), ("", TOKEN)])
def test_nothing_is_sent_while_off(
    settings: Any, sent: list[dict[str, Any]], domain: str, token: str
) -> None:
    """Both the domain and the token are needed."""
    settings.FIVEBAR_DOMAIN, settings.FIVEBAR_TOKEN = domain, token
    call(UserFactory.create(), "live_departures", {})
    assert sent == []
    assert not counts_mcp()


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(events.ENDPOINT, 503, "unavailable", {}, None),  # type: ignore[arg-type]
        urllib.error.URLError("no route"),
        TimeoutError(),
    ],
    ids=["http", "network", "timeout"],
)
def test_a_failed_send_is_logged_not_raised(error: Exception, caplog: Any) -> None:
    """A fivebar that is down or slow never reaches the caller."""
    with mock.patch.object(events.urllib.request, "urlopen", side_effect=error):
        events._post({"name": "/mcp/live_departures"}, TOKEN)
    assert "fivebar.event" in caplog.text


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
def test_null_arguments_are_no_arguments(sent: list[dict[str, Any]]) -> None:
    """A call with "arguments": null runs the tool and counts it."""
    call(UserFactory.create(), "live_departures", None)  # type: ignore[arg-type]
    assert [r["body"]["name"] for r in sent] == ["/mcp/live_departures"]


@pytest.mark.django_db
@pytest.mark.usefixtures("on")
def test_events_past_the_backlog_are_dropped(monkeypatch: Any, caplog: Any) -> None:
    """While fivebar is stuck, the queue stops growing; once a send ends, its place frees."""
    submitted: list[Any] = []
    monkeypatch.setattr(events, "_pending", events.threading.BoundedSemaphore(2))
    monkeypatch.setattr(events, "_pool", mock.Mock(submit=lambda *a: submitted.append(a)))
    user = UserFactory.create()
    for _ in range(3):
        events.tool_called(user, "live_departures", events.OK)
    assert len(submitted) == 2
    assert "dropped=backlog" in caplog.text
    with mock.patch.object(events, "_post"):
        func, event, token = submitted[0]
        func(event, token)
    events.tool_called(user, "live_departures", events.OK)
    assert len(submitted) == 3


@pytest.mark.parametrize(
    ("name", "client"),
    [("Claude", "claude"), (" ChatGPT ", "chatgpt"), ("", ""), (None, "")],
)
def test_client_name_is_the_apps_name_lower_cased(name: Any, client: str) -> None:
    """Claude's registered name becomes fivebar's "claude" source."""
    token = mock.Mock(application=mock.Mock())
    token.application.name = name
    assert events.client_name(token) == client
    assert events.client_name(None) == ""


def test_the_endpoint_passes_the_connected_app(
    client: Any, settings: Any, monkeypatch: Any
) -> None:
    """The view reads the app's name off the token and hands it to the dispatcher."""
    seen: list[str] = []

    def dispatch(user: Any, method: str, params: Any, client: str = "") -> dict[str, Any]:
        seen.append(client)
        return {}

    monkeypatch.setattr(views, "_dispatch", dispatch)
    monkeypatch.setattr(events, "client_name", lambda token: "claude")
    request = mock.Mock(
        method="POST",
        headers={},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
    )
    inspect.unwrap(views.mcp)(request)  # past @mcp_endpoint: the auth contract covers it
    assert seen == ["claude"]


def test_the_privacy_policy_says_what_tool_calls_send() -> None:
    """The privacy page, and so the privacy_policy tool, says what fivebar receives."""
    text = page_text("/privacy")
    assert "uses one of Traintrackr's tools" in text
    assert "never what you asked or what came back" in text


def test_the_sender_never_holds_up_a_stopping_process() -> None:
    """Its threads are daemons, so the process can exit with sends still queued."""
    sender = events._Sender(workers=1)
    ran = threading.Event()
    sender.submit(lambda: None)
    sender.submit(ran.set)
    assert ran.wait(timeout=5)
    threads = [t for t in threading.enumerate() if t.name == "fivebar-0"]
    assert threads
    assert all(t.daemon for t in threads)


def test_a_failing_send_does_not_stop_the_sender(caplog: Any) -> None:
    """An unexpected error is logged and the next send still runs."""
    sender = events._Sender(workers=1)
    ran = threading.Event()

    def broken() -> None:
        raise RuntimeError

    sender.submit(broken)
    sender.submit(ran.set)
    assert ran.wait(timeout=5)
    assert "fivebar.event failed" in caplog.text
