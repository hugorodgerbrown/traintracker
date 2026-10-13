"""Tests for the MCP endpoint: the shared auth contract, then the train tools."""

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import Client
from mcp_auth.testing import MCPAuthContract

from apps.mcp.resources import MIME_TYPE, RESOURCES
from apps.mcp.tools import TOOLS, ToolError, _run_train_tool
from apps.rail import server
from tests.factories import UserFactory


class TestMCPAuth(MCPAuthContract):
    """Every Titan MCP server passes the same OAuth contract."""

    mcp_path = "/mcp"

    def make_allowed_user(self, django_user_model: Any) -> Any:
        """Anyone with an active account may connect (MCP_AUTH CAN_CONNECT)."""
        return UserFactory.create()

    def make_refused_user(self, django_user_model: Any) -> Any:
        """A closed account may not."""
        return UserFactory.create(is_active=False)


@pytest.fixture
def rpc(user: Any) -> Any:
    """Call the MCP view as ``user``, bypassing OAuth (the contract covers that)."""
    from apps.mcp import views

    def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = views._dispatch(user, method, params or {})
        return result

    return call


def test_initialize_names_the_server_and_says_how_to_use_it(rpc: Any) -> None:
    """Initialize gives the same instructions as production's server."""
    result = rpc("initialize")
    assert result["serverInfo"]["name"] == "traintracker"
    assert result["instructions"] == server.INSTRUCTIONS


def test_every_train_tool_is_listed(rpc: Any) -> None:
    """tools/list lists the same tools as production's server, with nothing added."""
    names = {t["name"] for t in rpc("tools/list")["tools"]}
    assert names == set(TOOLS)
    assert names == {info.name for info in server.mcp._tool_manager.list_tools()}


def test_tools_carry_titles_hints_and_schemas(rpc: Any) -> None:
    """Every tool reads only, says so, and has the title Claude's directory review checks."""
    for described in rpc("tools/list")["tools"]:
        annotations = described["annotations"]
        assert described["title"] == annotations["title"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
        assert annotations["idempotentHint"] is True
        assert described["inputSchema"]["type"] == "object"
        assert described["outputSchema"]["type"] == "object"


def test_only_live_tools_reach_out(rpc: Any) -> None:
    """The open-world hint is set on the tools that can call Darwin, and no others."""
    tools = {t["name"]: t["annotations"]["openWorldHint"] for t in rpc("tools/list")["tools"]}
    closed = {name for name, open_world in tools.items() if not open_world}
    assert closed == {"find_station", "timetable", "data_status", "privacy_policy"}


def test_initialize_advertises_resources(rpc: Any) -> None:
    """The server says it has resources and MCP Apps, so the client renders a tool's UI."""
    capabilities = rpc("initialize")["capabilities"]
    assert "resources" in capabilities
    assert capabilities["extensions"]["io.modelcontextprotocol/ui"] == {"mimeTypes": [MIME_TYPE]}


def test_resources_list_every_view(rpc: Any) -> None:
    """resources/list lists every ui:// view as an MCP App."""
    listed = rpc("resources/list")["resources"]
    assert {r["uri"] for r in listed} == set(RESOURCES)
    assert all(r["uri"].startswith("ui://") and r["mimeType"] == MIME_TYPE for r in listed)
    assert rpc("resources/templates/list") == {"resourceTemplates": []}


def test_only_show_board_draws_the_board(rpc: Any) -> None:
    """A client draws a tool's view on every call, so only show_board names one."""
    with_ui = {t["name"]: t["_meta"] for t in rpc("tools/list")["tools"] if "_meta" in t}
    assert list(with_ui) == ["show_board"]
    uri = with_ui["show_board"]["ui"]["resourceUri"]
    assert uri in RESOURCES
    assert with_ui["show_board"]["ui/resourceUri"] == uri


def test_the_board_is_production_s_board(rpc: Any) -> None:
    """The board is read as it is: one document, its own bridge, nothing loaded."""
    from apps.rail import ui

    (content,) = rpc("resources/read", {"uri": ui.BOARD_URI})["contents"]
    assert content["mimeType"] == MIME_TYPE
    assert content["_meta"]["ui"] == {"prefersBorder": False}
    html = content["text"]
    assert html == ui.board_html()
    assert not re.search(r"<(script|img|iframe)[^>]*\ssrc=|<link\b", html)


@pytest.mark.parametrize(
    ("params", "code"),
    [({"uri": "ui://nope"}, -32002), ({}, -32602)],
)
def test_resources_read_errors(rpc: Any, params: dict[str, Any], code: int) -> None:
    """An unknown URI is 'resource not found'; a missing one is invalid params."""
    from apps.mcp.views import RpcError

    with pytest.raises(RpcError) as caught:
        rpc("resources/read", params)
    assert caught.value.code == code


def test_inlined_scripts_cannot_close_their_tag(tmp_path: Any, monkeypatch: Any) -> None:
    """A "</script>" inside a view's JavaScript is escaped, not left to end the tag."""
    from apps.mcp import resources

    (tmp_path / "v.html").write_text("<body></body>")
    (tmp_path / "v.js").write_text("const s = '</script>';")
    monkeypatch.setattr(resources, "UI_DIR", tmp_path)
    html = resources.render.__wrapped__("v.html", ("v.js",))
    assert html == "<body><script>\nconst s = '<\\/script>';</script>\n</body>"


def test_a_view_needs_a_body(tmp_path: Any, monkeypatch: Any) -> None:
    """HTML with nowhere to put the scripts is a mistake, caught at once."""
    from apps.mcp import resources

    (tmp_path / "v.html").write_text("<p>no body</p>")
    monkeypatch.setattr(resources, "UI_DIR", tmp_path)
    with pytest.raises(ValueError, match="no </body>"):
        resources.render.__wrapped__("v.html", ())


@pytest.mark.usefixtures("train_tools")
def test_find_station(rpc: Any) -> None:
    """A train tool answers through Titan's view: JSON text, and the same as structured."""
    result = rpc(
        "tools/call", {"name": "find_station", "arguments": {"query": "Liverpool St", "limit": 1}}
    )
    assert "isError" not in result
    (match,) = result["structuredContent"]["result"]
    assert match["crs"] == "LST"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


@pytest.mark.usefixtures("train_tools")
def test_live_departures_on_demo_data(rpc: Any) -> None:
    """A live board comes from the shared Darwin client (demo mode answers in-process)."""
    result = rpc(
        "tools/call", {"name": "live_departures", "arguments": {"station": "LST", "rows": 2}}
    )
    board = result["structuredContent"]
    assert board["station"]["crs"] == "LST"
    assert board["source"] == "darwin"
    assert 0 < len(board["services"]) <= 2


@pytest.mark.usefixtures("train_tools")
def test_a_bad_argument_is_an_error_the_model_can_read(rpc: Any) -> None:
    """The server's schema check becomes an isError result, not a protocol error."""
    result = rpc("tools/call", {"name": "find_station", "arguments": {"query": "x" * 200}})
    assert result["isError"] is True
    assert "at most 80 characters" in result["content"][0]["text"]


@pytest.mark.usefixtures("train_tools")
def test_traintrackr_errors_reach_the_model(rpc: Any) -> None:
    """A name that matches no station says so, as production's server does."""
    result = rpc("tools/call", {"name": "live_departures", "arguments": {"station": "Qqqqqq"}})
    assert result["isError"] is True
    assert result["content"][0]["text"]


@pytest.mark.usefixtures("train_tools")
def test_each_account_has_its_own_bucket_of_calls(rpc: Any, monkeypatch: Any) -> None:
    """Past its burst an account is told how long to wait; other accounts are not."""
    from apps.mcp import tools
    from apps.rail.ratelimit import RateLimiter

    buckets = RateLimiter(per_minute=60, burst=2, clock=lambda: 0.0)
    monkeypatch.setattr(tools, "limiter", lambda: buckets)
    call = {"name": "find_station", "arguments": {"query": "Cambridge"}}
    for _ in range(2):
        assert "isError" not in rpc("tools/call", call)
    refused = rpc("tools/call", call)
    assert refused["isError"] is True
    assert refused["content"][0]["text"] == "Too many requests. Try again in 1 second."
    other = tools._run_train_tool(SimpleNamespace(pk=-1), "find_station", {"query": "Ely"})
    assert other["result"]


@pytest.mark.usefixtures("train_tools")
def test_a_journey_plan_costs_three_calls(monkeypatch: Any) -> None:
    """Planning a journey scans the day's trains several times over, so it costs more."""
    from apps.mcp import tools
    from apps.rail.ratelimit import RateLimiter

    buckets = RateLimiter(per_minute=60, burst=4, clock=lambda: 0.0)
    monkeypatch.setattr(tools, "limiter", lambda: buckets)
    account = SimpleNamespace(pk=1)
    tools._run_train_tool(account, "find_station", {"query": "Ely"})
    tools._run_train_tool(account, "find_station", {"query": "Ely"})
    with pytest.raises(ToolError, match="Try again in 1 second"):
        tools._run_train_tool(account, "plan_journey", {"origin": "CBG", "destination": "ELY"})


def test_a_call_that_takes_too_long_says_try_again(monkeypatch: Any) -> None:
    """A call over the time limit is cancelled and the model is told to retry."""
    from apps.rail import runtime

    def slow(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError

    monkeypatch.setattr(runtime, "run", slow)
    with pytest.raises(ToolError, match="busy"):
        _run_train_tool(SimpleNamespace(pk=1), "find_station", {"query": "x"})


def test_an_unexpected_error_tells_the_model_nothing_of_the_server(monkeypatch: Any) -> None:
    """The SDK's unexpected errors can carry internals; the model gets a plain message."""
    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    from apps.rail import runtime

    def broken(*args: Any, **kwargs: Any) -> Any:
        try:
            raise KeyError("/srv/secret/path")
        except KeyError as exc:
            raise UnexpectedToolError("Error executing tool x: /srv/secret/path") from exc

    monkeypatch.setattr(runtime, "run", broken)
    with pytest.raises(ToolError) as caught:
        _run_train_tool(SimpleNamespace(pk=1), "find_station", {"query": "x"})
    assert str(caught.value) == "Something went wrong on the server. Try again."


def call_endpoint(client: Client, body: Any, headers: dict[str, str] | None = None) -> Any:
    """POST ``body`` to /mcp with a valid bearer token for an ordinary user."""
    token = TestMCPAuth().token_for(UserFactory.create())
    return client.post(
        "/mcp",
        body if isinstance(body, str) else json.dumps(body),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}", **(headers or {})},
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ("{nope", 400, -32700),
        ({"jsonrpc": "1.0"}, 400, -32600),
        ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, 200, -32601),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": "x"}, 200, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}, 200, -32602),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "find_station", "arguments": "x"},
            },
            200,
            -32602,
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://x"}},
            200,
            -32002,
        ),
    ],
)
def test_protocol_errors(client: Client, body: Any, status: int, code: int) -> None:
    """Malformed JSON-RPC gets the standard error codes."""
    response = call_endpoint(client, body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.django_db
def test_notifications_get_202_and_old_versions_are_refused(client: Client) -> None:
    """A notification needs no answer; an unknown protocol version is refused."""
    assert call_endpoint(client, {"jsonrpc": "2.0", "method": "initialized"}).status_code == 202
    response = call_endpoint(
        client,
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"MCP-Protocol-Version": "1999-01-01"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_ping_with_a_token(client: Client) -> None:
    """A valid token reaches the tools."""
    response = call_endpoint(client, {"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert response.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "callback",
    [
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://platform.openai.com/apps-manage/oauth",
    ],
)
def test_chatgpt_can_register(client: Client, callback: str) -> None:
    """ChatGPT's callbacks are allowed as well as Claude's: Traintrackr is in its directory."""
    response = TestMCPAuth().register(client, [callback])
    assert response.status_code == 201, response.content


@pytest.mark.django_db
def test_endpoint_refuses_anonymous_calls(client: Client) -> None:
    """No bearer token, no tools: a 401 that starts OAuth discovery."""
    response = client.post(
        "/mcp",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert "resource_metadata" in response["WWW-Authenticate"]


def test_staff_only_policy() -> None:
    """The template's staff-only rule still works, though Traintrackr doesn't use it."""
    from types import SimpleNamespace

    from apps.mcp.policy import staff_only

    assert staff_only(SimpleNamespace(is_active=True, is_staff=True))
    assert not staff_only(SimpleNamespace(is_active=True, is_staff=False))
    assert not staff_only(SimpleNamespace(is_active=False, is_staff=True))
