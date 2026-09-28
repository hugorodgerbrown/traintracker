# traintracker

An MCP server that lets Claude answer questions about GB (National Rail) trains: *"when's the next train to Sudbury?"*, *"is the 13:00 from Liverpool Street on time?"*, *"how do I get from Cambridge to Sudbury next Saturday morning?"*

It runs locally over stdio for the Claude desktop app, or hosted over HTTP (see [Deploy to Render](#deploy-to-render)). The timetable is stored in Postgres.

```mermaid
flowchart LR
    Claude[Claude] <-->|stdio or HTTP| TT[traintracker]

    subgraph Live["Live, next ~2 hours"]
        D[Darwin<br/>Rail Data Marketplace]
    end
    subgraph Timetable["Timetable, any future date"]
        NR[Network Rail<br/>SCHEDULE feed] -->|daily download| DB[(Postgres<br/>timetable schema)]
    end
    TT --> D
    TT --> DB
```

## Tools

| Tool | Answers | Data |
|---|---|---|
| `find_station` | "Which Sudbury?" Station names ↔ CRS codes | Bundled station list |
| `live_departures` | Next trains, expected times, platforms, delays, cancellations | Darwin; if it's not set up, booked times; if it's offline, booked times with a note saying so |
| `departure_platform` | "Which platform is the 13:00 to Colchester?" One train's platform, flagged `live` or `booked` | Darwin (paged); booked platform from the timetable until the live one is announced |
| `platform_departures` | "What are the next three trains from platform 7?" | Same as `departure_platform` |
| `live_arrivals` | Trains arriving in the next ~2 hours | Darwin arrivals; if not set up, booked times; if offline, booked times with a note saying so |
| `timetable` | Booked departures/arrivals at a station on any date | Local timetable |
| `service_details` | Every stop for one train | Whichever source issued the ID |
| `plan_journey` | A to B with changes (up to `max_changes`, default 4), incl. cross-London links | Local timetable + Darwin live overlay |
| `data_status` | What's configured, timetable freshness, what's missing | — |

Every tool accepts station names or CRS codes. Ambiguous names ("Sudbury", "Harrow") return the candidates so Claude can ask which one you meant.

## Try it without accounts (demo mode)

Set `TRAINTRACKER_DEMO=1` to test every tool before your accounts are approved. The server then uses generated example data and makes no network requests:

- **Timetable:** a generated SCHEDULE feed for 25 real stations in East Anglia and London (Liverpool Street, Stratford, Chelmsford, Colchester, Ipswich, Norwich, Marks Tey, Sudbury, Cambridge, Kings Cross and others), running from today for 90 days. Sundays start later. It is stored in its own schema (`timetable_demo`), apart from the real timetable, and rebuilt each day. Demo mode still needs `DATABASE_URL`.
- **Live boards:** Darwin departure, arrival and service-details responses are generated from that timetable in-process. About one train in five runs late, one in 25 is cancelled and one in 12 has a platform change; the same train on the same day always gets the same result. Large stations (Liverpool Street, Kings Cross, Cambridge, …) announce platforms 15 minutes before departure, so the booked-platform fallback gets exercised.

Every board and plan carries a note that the data is generated, and `data_status` reports `demo_mode`. Ask for stations outside the demo network and you get an empty board.

```json
"env": { "TRAINTRACKER_DEMO": "1" }
```

Add that to the `traintracker` entry in the Claude desktop config (see [Add to the Claude desktop app](#add-to-the-claude-desktop-app)), or put `TRAINTRACKER_DEMO=1` in `.env`. Remove it once your keys are in place. Things to try: *"next trains from Liverpool Street"*, *"which platform is the next train from Cambridge to Kings Cross?"*, *"how do I get from Cambridge to Sudbury tomorrow at 9?"*

## Accounts you need

| # | Account | Needed? | Cost | What it provides | Used for |
|---|---|---|---|---|---|
| 1 | **Rail Data Marketplace** — *Live Departure Board* product | **Yes** for live times | Free | Darwin: National Rail's real-time feed. Departure boards for the next 2 hours with expected times, platforms, delay/cancellation reasons, calling points, station alerts. The same data as station screens. | `live_departures`, live overlay in `plan_journey` |
| 2 | **Network Rail Open Data (NTROD)** — *SCHEDULE* feed | **Yes** for timetables and planning | Free | The full GB passenger timetable from Network Rail's planning system, including short-term changes (engineering works, extra trains, cancellations) once published. Refreshed daily. | `timetable`, `plan_journey`, `service_details` for `tt:` IDs, offline fallback for the live tools |
| 3 | Rail Data Marketplace — *Service Details* product | Optional | Free | Full live calling pattern for a train seen on a Darwin board. | `service_details` for `darwin:` IDs |
| 4 | Rail Data Marketplace — *Live Arrival Board* product | Optional | Free | Darwin arrivals boards. Without it, arrivals show booked times. | `live_arrivals` |

### 1. Rail Data Marketplace (Darwin)

1. Create an account at [raildata.org.uk](https://raildata.org.uk).
2. In the product catalogue, search **"Live Departure Board"** (the LDBWS product, not "Live *Fastest* Departure Boards") and subscribe. Approval for the free tier ranges from instant to a few days.
3. Open the subscribed product → **Specification** tab. Copy the **Consumer key** (not the secret) into `DARWIN_API_KEY`.
4. Check the base URL on that tab matches `DARWIN_DEPARTURES_URL` in `.env.example`. If it differs, set `DARWIN_DEPARTURES_URL` to your value (everything before `/GetDepBoardWithDetails`).
5. *(Optional)* Subscribe to **Service Details** and **Live Arrival Board** the same way. Each product has its own key and URL: set `DARWIN_SERVICE_API_KEY` / `DARWIN_SERVICE_URL` and `DARWIN_ARRIVALS_API_KEY` / `DARWIN_ARRIVALS_URL`.

### 2. Network Rail Open Data (SCHEDULE)

1. Register at the [Network Rail data feeds portal](https://publicdatafeeds.networkrail.co.uk/ntrod/welcome). Activation can take a while.
2. Sign in and check that **SCHEDULE** appears under *Static feeds*.
3. Put your portal username and password in `NR_USERNAME` and `NR_PASSWORD`.
4. If the portal gives a different download link for the *full daily extract, all operators, JSON* than `NR_SCHEDULE_URL` in `.env.example`, set `NR_SCHEDULE_URL` to it.

The server downloads the feed in the background when the timetable is missing or older than 26 hours. It checks this on start-up and on every tool call, so a server left running for days stays current. If a download fails it keeps serving the old timetable, and `data_status` shows the error. You can also run `traintracker refresh` by hand (see [Commands](#commands)). Set `TIMETABLE_AUTO_REFRESH=0` where a separate job runs `traintracker refresh`, as on Render.

### 3. Postgres

The timetable lives in one schema (`timetable` by default) of the database in `DATABASE_URL`. The role needs to create schemas in that database; the owner of the database can. On a shared server, give traintracker its own database and role:

```sql
CREATE ROLE traintracker LOGIN PASSWORD '…';
CREATE DATABASE traintracker OWNER traintracker;
REVOKE CONNECT ON DATABASE traintracker FROM PUBLIC;
```

The full GB timetable takes about 475 MB (4.3 million stops), and about twice that while a refresh builds the new copy. A refresh takes about 40 seconds plus the download. It builds the new timetable in a staging schema and swaps it in with a rename, so readers never see a half-built timetable, and an advisory lock stops two refreshes running at once.

The tables are `UNLOGGED`: they are rebuilt from the feed every day, so they skip the write-ahead log and don't add to the server's WAL or point-in-time-recovery storage. The cost is that Postgres empties them after a crash; the tools then report "No timetable yet" until the next refresh.

## How each source is used

```mermaid
flowchart TD
    Q{What's being asked?}
    Q -->|next ~2 hours| L[live_departures / live_arrivals]
    Q -->|a future date| T[timetable]
    Q -->|A to B| P[plan_journey]
    Q -->|one train| S[service_details]

    L --> L1{Darwin key?}
    L1 -->|yes| Darwin
    Darwin -->|error or timeout:<br/>note says Darwin is offline| TT
    L1 -->|no| TT[(Timetable<br/>booked times, flagged)]

    T --> TT

    P --> CSA[Connection Scan<br/>over the day's timetable]
    CSA --> OV{Today, departing<br/>within 2 hours?}
    OV -->|yes| Darwin2[Darwin expected times<br/>+ tight-connection flag]
    Darwin2 -.->|offline| Note[Plan note: Darwin offline,<br/>times are booked]

    S --> ID{ID prefix}
    ID -->|darwin:| Darwin3[Darwin Service Details]
    ID -->|tt:| TT
```

| Source | Freshness | Coverage | Cached for | Limits to know |
|---|---|---|---|---|
| Darwin | Real time | Now → +2 hours | 20 s | Darwin service IDs expire soon after the train runs |
| Timetable (Postgres) | Daily (Network Rail publishes ~06:00) | Two days back → end of the published timetable (usually months) | Until the next rebuild | Booked times only; last-minute changes show up in Darwin, not here |

### What the timetable keeps

The SCHEDULE feed is large (all trains, freight included). On import, traintracker keeps only what a passenger needs:

- Passenger categories only (trains, replacement/timetabled buses, ships). Freight and empty stock are dropped, except overlay and cancellation records, which can stop a passenger train running on a given day.
- Stops with a public time only; junctions and passing points are dropped.
- Schedules that ended more than two days ago are dropped.

For each date it applies Network Rail's precedence rules per train: cancellation (C) beats new (N), which beats overlay (O), which beats permanent (P), and it honours each schedule's running days and date range. Bank-holiday running flags are not applied yet, so on bank holidays check the live board.

### Journey planning

`plan_journey` uses a round-based Connection Scan over every train hop of the day: round *n* finds the earliest arrival using at most *n* trains. That gives the fastest journey and the slower options with fewer changes in one pass, up to `max_changes`. It repeats from just after each departure to find the next few options, then drops any option that another beats on departure, arrival and changes at once, so you're never told to leave earlier than necessary. Results are ordered by arrival, and the fewest-changes option is always included.

- **Minimum change time:** 5 minutes between trains by default (`MIN_INTERCHANGE_MINUTES`). Walk/Tube links already include time to get in and out of stations, so no extra change time is added after them.
- **Cross-London:** the Tube isn't in the rail timetable, so transfers between London terminals (Kings Cross, Liverpool Street, Waterloo, etc.) are approximated: walks under 0.8 km at walking pace plus 5 minutes, otherwise Tube at ~12 minutes plus 3.5 minutes per km. They're labelled `tube (approx.)`.
- **Live overlay:** for today's legs departing within two hours, Darwin's expected times and platforms are added, and `connection_at_risk` is set if a delay or cancellation eats into a change, including one made via a walk/Tube link. If Darwin is down, the plan is still returned without live times.
- The search covers the service day (trains running into the early hours are included); it doesn't carry over to the next morning. Station boards do include trains just after midnight.

## Install

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and a Postgres database (any version from 13). Locally, Docker (or OrbStack) is the quickest way to get one:

```bash
docker run -d --name traintracker-pg --restart unless-stopped \
  -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:55432:5432 postgres:17
```

```bash
git clone git@github.com:hugorodgerbrown/traintracker.git
cd traintracker
uv sync
cp .env.example .env          # fill in your keys and DATABASE_URL (.env is git-ignored)
uv run traintracker refresh   # first timetable download (a few minutes)
uv run traintracker status
```

### Add to the Claude desktop app

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`. Use the full path to `uv` (run `which uv`), because the app doesn't use your shell's `PATH`:

```json
{
  "mcpServers": {
    "traintracker": {
      "command": "/Users/hugo/.local/bin/uv",
      "args": ["--directory", "/Users/hugo/Projects/traintracker", "run", "traintracker"]
    }
  }
}
```

`--directory` makes the project folder the working directory, so the server reads your keys from `.env` there. Keys can go in an `"env"` block in this file instead; real environment variables take precedence over `.env`.

Restart the Claude app, then ask *"what's the status of traintracker's data sources?"* to check everything is connected.

## Configuration

All configuration is by environment variable, read from `.env` in the project folder if present. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DARWIN_API_KEY` | — | Live Departure Board consumer key |
| `DARWIN_DEPARTURES_URL` | RDM LDBWS URL | Override if your Specification tab differs |
| `DARWIN_SERVICE_API_KEY` / `DARWIN_SERVICE_URL` | board key / RDM URL | Service Details product |
| `DARWIN_ARRIVALS_API_KEY` / `DARWIN_ARRIVALS_URL` | — | Live Arrival Board product |
| `NR_USERNAME` / `NR_PASSWORD` | — | Network Rail data feeds login |
| `NR_SCHEDULE_URL` | full daily JSON extract | Override if the portal gives a different link |
| `DATABASE_URL` | — | Postgres database for the timetable (required) |
| `TIMETABLE_SCHEMA` | `timetable` | Schema the timetable lives in (demo mode appends `_demo`) |
| `TIMETABLE_AUTO_REFRESH` | `1` | `0` stops the server downloading the timetable itself (use a cron job instead) |
| `TIMETABLE_MAX_AGE_HOURS` | `26` | Re-download when older than this |
| `TRAINTRACKER_DATA_DIR` | `$XDG_DATA_HOME/traintracker` if set, else `~/.traintracker` | Where the feed is downloaded to before import |
| `MCP_AUTH_TOKEN` | — | Static bearer token accepted by `serve-http` |
| `MCP_OAUTH_PASSPHRASE` | — | Passphrase for the OAuth sign-in page, which lets claude.ai add the server as a connector. `serve-http` needs this, `MCP_AUTH_TOKEN`, or both |
| `MCP_PUBLIC_URL` | `https://` + first public host | Base URL clients use; the OAuth issuer and resource (`<url>/mcp`) derive from it |
| `MCP_AUTH_SCHEMA` | `mcp_auth` | Schema for OAuth clients, codes and tokens (tokens stored as SHA-256 hashes) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Where `serve-http` listens (bind address) |
| `MCP_PUBLIC_HOSTS` | — | Comma-separated extra hostnames clients use (e.g. a custom domain), added to `RENDER_EXTERNAL_HOSTNAME`; `serve-http` rejects other `Host` headers (DNS-rebinding protection) |
| `MIN_INTERCHANGE_MINUTES` | `5` | Minimum change time for planning |
| `HTTP_TIMEOUT_SECONDS` | `15` | Upstream request timeout |
| `TRAINTRACKER_DEMO` | off | `1` uses generated example data instead of any account (see [demo mode](#try-it-without-accounts-demo-mode)) |

## Commands

| Command | Does |
|---|---|
| `traintracker` | Run the MCP server on stdio (what Claude runs) |
| `traintracker serve-http` | Run the MCP server over streamable HTTP at `/mcp`, behind OAuth sign-in and/or a static bearer token, with a `/healthz` check |
| `traintracker refresh` | Download the SCHEDULE feed and rebuild the timetable (in demo mode, regenerate the demo timetable) |
| `traintracker import FILE.json.gz` | Build the timetable from a feed file you downloaded yourself |
| `traintracker status` | Show configured sources and timetable details |

Logs go to stderr; stdout carries the MCP protocol.

## Deploy to Render

[`render.yaml`](render.yaml) defines two services in Frankfurt:

| Service | Type | Does | Environment |
|---|---|---|---|
| `traintracker` | Web service, 512 MB, [traintrackr.live](https://traintrackr.live) | `serve-http`; health check `/healthz` | `DATABASE_URL`, `DARWIN_API_KEY`, `MCP_OAUTH_PASSPHRASE`, `MCP_PUBLIC_HOSTS`, `TIMETABLE_AUTO_REFRESH=0` |
| `traintracker-refresh` | Cron job, 06:30 UTC daily | `traintracker refresh` | `DATABASE_URL`, `NR_USERNAME`, `NR_PASSWORD` |

The web service holds one day's journey network in memory for `plan_journey` (about 200 MB), so it needs at least 512 MB.

1. Create the `traintracker` database and role on your Postgres instance (see [Postgres](#3-postgres)).
2. In the Render dashboard: **New → Blueprint**, pick this repository, and enter the values Render prompts for. Use the Postgres instance's *internal* URL, with `/traintracker` as the database name.
3. Run the cron job once by hand (**Trigger Run**) to load the first timetable.
4. Add the server as a claude.ai connector (below). Use your own service's host: its `onrender.com` name, or a custom domain you have added to the service and listed in `MCP_PUBLIC_HOSTS` (the `render.yaml` value is this repository's deployment, `traintrackr.live`).

For a client that can't do OAuth, set `MCP_AUTH_TOKEN` on the service to a long random value; it is accepted as a bearer token alongside OAuth, and never expires. For example, in a terminal (so the token stays out of any transcript), with the token on the clipboard:

```bash
claude mcp add -s user --transport http traintracker https://<your-service-host>/mcp --header "Authorization: Bearer $(pbpaste)"
```

### Add as a claude.ai connector

With `MCP_OAUTH_PASSPHRASE` set, the server is its own OAuth authorization server, so it can be added once in claude.ai and used from claude.ai, Claude Desktop and the mobile apps.

1. In claude.ai: **Settings → Connectors → Add custom connector**, name `traintracker`, URL `https://<your-service-host>/mcp`. Leave the OAuth client fields empty: Claude registers itself.
2. Claude opens the server's sign-in page. Enter the passphrase and click **Allow**.

Sign-in hands Claude a one-hour access token and a 90-day refresh token, rotated on each refresh. Five wrong passphrases discard the sign-in attempt. Clients, codes and tokens are stored in the `mcp_auth` schema, tokens as SHA-256 hashes. To sign every client out, run `TRUNCATE mcp_auth.tokens` against the database. Changing the passphrase doesn't sign anyone out; truncate the tokens as well.

## Limitations

- **Past running times** ("was the 08:00 late yesterday?") aren't available. Darwin only covers now → +2 hours, and the timetable is booked times.
- **Darwin outages**: when Darwin is offline, the live tools show booked times and say that Darwin is offline, so delays, cancellations and live platforms are missing until it returns.
- **Fares** aren't included.
- **Tube/bus/tram** aren't in the timetable beyond the approximate London terminal links.
- **Engineering works** appear in the timetable once Network Rail publishes them (usually well ahead), and in Darwin on the day.

## Development

tox runs formatting, lint, type and test checks on Python 3.11 from `uv.lock` (via the `tox-uv` plugin). The tests need a Postgres they can create schemas in, at `TEST_DATABASE_URL` (default `postgresql://postgres:postgres@127.0.0.1:55432/postgres`, which the `docker run` under [Install](#install) provides). Each test works in its own schema and drops it afterwards. CI runs Postgres 17 as a service.

```bash
uvx --with tox-uv tox              # all environments: format, lint, type, tests
uvx --with tox-uv tox -e tests     # one environment
uvx --with tox-uv tox -e tests -- -k platform   # arguments after -- go to the tool
```

| Environment | Runs |
|---|---|
| `format` | `ruff format --check` |
| `lint` | `ruff check` |
| `type` | `mypy` (strict) |
| `tests` | `pytest`: importer, STP rules, planner, download, clients, demo mode, tools end to end |

Tests use a synthetic SCHEDULE feed in Network Rail's JSON format (`tests/feedgen.py`) and API fixtures shaped on the published Darwin schema. They aren't live recordings, so the first run against real services is the final check.

```
src/traintracker/
  server.py      MCP tools, source fallback, live overlay, CLI
  http_app.py    Streamable-HTTP entry point: auth wiring, Host check, health check
  oauth.py       OAuth provider (Postgres) and passphrase sign-in page
  timetable.py   SCHEDULE importer and Postgres queries (STP resolution)
  planner.py     Connection Scan journey planner, London links
  darwin.py      Rail Data Marketplace LDBWS client
  stations.py    Station search and name resolution
  models.py      Output models shared by all sources
  demo.py        Demo mode: generated timetable and in-process Darwin
```

## Data and licences

- Darwin data via the Rail Data Marketplace, and Network Rail data feeds, are used under the terms you accept when you subscribe. Check those terms before redistributing any output.
- The bundled station list comes from [davwheat/uk-railway-stations](https://github.com/davwheat/uk-railway-stations) under the Open Database License (ODbL).

Code: MIT.
