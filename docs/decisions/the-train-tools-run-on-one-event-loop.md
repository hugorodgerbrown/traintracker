# The train tools run on one event loop

Status: accepted (2026-10-13)

## Context

Titan's `/mcp` view (`apps/mcp/views.py`) is synchronous: one JSON-RPC
message in, one JSON body out, under gunicorn's threaded workers. Traintrackr's
eleven tools are coroutines on the MCP SDK server in `apps/rail/server.py`
(`src/traintracker/server.py` until Titan 4/4). They share state that only
works once a process:

- one `httpx.AsyncClient` for Darwin, and the day's Darwin request budget;
- a semaphore that holds timetable reads and journey plans to two at a time;
- the background timetable refresh, started from the server's lifespan;
- the journey planner's in-memory network, the largest thing the process holds,
  and what decides whether the service fits in 512 MB (PR #42).

Mounting the SDK's own Streamable HTTP app inside Django was the other way.
Hugo chose Titan's view, so Traintrackr's MCP endpoint is the same code as
every other Titan project's, and mcp-auth's contract tests cover it.

## Decision

- **One loop a process, in a daemon thread** (`apps/rail/runtime.py`). The
  first tool call starts it and enters the SDK server's lifespan on it; a
  request thread hands its call to the loop and waits, up to 60 seconds.
- **The tools are listed from the SDK server, not rewritten.** `apps/mcp/tools.py`
  turns each registered tool into a Titan `Tool`: its schemas, title and hints
  come from `server.py`, and its function runs `mcp.call_tool` on the loop.
  Titan's `Tool` gains
  `title`, `output_schema` and `hints`, which Claude's directory review needs.
- **The board is served as it is.** `ui/board.html` has its own bridge, so
  its `UiResource` sets `bridge=False` instead of inlining Titan's `bridge.js`.
- **One gunicorn worker with threads** (`render.yaml`): more workers would
  each build the planner's network.
- **The rate limit is applied before the loop.** The SDK server's middleware
  limited each account when it served HTTP itself; `mcp.call_tool` bypasses
  middleware, so `apps/mcp/tools.py` takes the account's token and runs the
  call through `ratelimit.charged`, which charges its further Darwin requests
  to the same account.

## Consequences

- A slow tool holds a request thread, not the loop: other calls carry on.
  The semaphore still refuses heavy work after 10 seconds' wait.
- Results come back as Titan's view writes them: `structuredContent` as the
  SDK made it, and one JSON text block (the SDK wrote one block per list item).
- The tools can become plain Titan tools one at a time; the loop stays
  while they are coroutines. `manage.py serve_stdio` runs the same SDK
  server on stdio for the desktop app.
- `apps/mcp/tools.py` reads the SDK's tool registry (`_tool_manager`), a
  private name; `mcp` is pinned below 3, and the tests compare the two lists.
