# Traintrackr

An MCP server that lets Claude and ChatGPT answer questions about GB (National Rail) trains: *"when's the next train to Sudbury?"*, *"is the 13:00 from Liverpool Street on time?"*, *"how do I get from Cambridge to Sudbury next Saturday morning?"*

The public service runs at [traintrackr.live](https://traintrackr.live). It is free, and you sign in with an email address; there is no password. The connector address is:

```
https://traintrackr.live/mcp
```

## Use it

**Claude.** Traintrackr is listed in Claude's connector directory: open [claude.ai/directory/traintrackr](https://claude.ai/directory/traintrackr), or in claude.ai open **Customize → Connectors**, choose the **+** next to Connectors and find *Traintrackr* in the directory. Choose **Connect**: Claude opens the Traintrackr sign-in page, where you enter your email address and then open the link, or type the six-digit code, in the email it sends. A connector added in claude.ai also works in Claude Desktop and the mobile apps. To add it by address instead, choose **Add custom connector**, name it *Traintrackr* and paste the connector address.

**ChatGPT.** ChatGPT adds it through developer mode, on the web, in Plus, Pro, Business, Enterprise and Edu plans; in a workspace, an admin may have to allow developer mode first. Turn on **Settings → Security and login → Developer mode**, then at [chatgpt.com/plugins](https://chatgpt.com/plugins) choose **+**, name it *Traintrackr*, paste the connector address and choose OAuth. Sign in with your email address as above. In a new chat, choose **+ → Developer mode** and select Traintrackr.

Then ask about trains. [traintrackr.live/docs](https://traintrackr.live/docs) shows what each answer contains, and the limits. The [privacy policy](https://traintrackr.live/privacy) and [terms of use](https://traintrackr.live/terms) are on the site too. The server is listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as `live.traintrackr/traintracker`.

The rest of this README describes how the server works and how to run your own copy: locally over stdio for the Claude desktop app, or hosted over HTTP (see [Deploy to Render](#deploy-to-render)). The timetable is stored in Postgres.

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
| `live_departures` | Next trains, expected times, platforms, delays, cancellations | Darwin, going on with scheduled timetable trains where fewer than `rows` run in its two hours; if it's not set up, scheduled times; if it's offline, scheduled times with a note saying so |
| `departure_platform` | "Which platform is the 13:00 to Colchester?" One train's platform, flagged `live` or `scheduled` | Darwin (paged); scheduled platform from the timetable until the live one is announced |
| `platform_departures` | "What are the next three trains from platform 7?" | Same as `departure_platform` |
| `live_arrivals` | Trains arriving in the next ~2 hours | Darwin arrivals, going on with scheduled trains where short; if not set up, scheduled times; if offline, scheduled times with a note saying so |
| `show_board` | "Show me the departures board for Liverpool Street." The same live departures or arrivals, drawn as a [departure board](#departure-board) | Same as `live_departures` and `live_arrivals` |
| `timetable` | Scheduled departures/arrivals at a station on any date | Local timetable |
| `service_details` | Every stop for one train | Whichever source issued the ID |
| `plan_journey` | A to B with changes (up to `max_changes`, default 4), incl. cross-London links | Local timetable + Darwin live overlay |
| `data_status` | What's configured, timetable freshness, what's missing; with `DJANGO_DEBUG=1` (a server you run yourself), also the timetable schema, the last refresh error and the Darwin allowance used | — |
| `privacy_policy` | "What do you keep about me?" The privacy policy as text, with the page's address | The `/privacy` page |

Every tool accepts station names or CRS codes. Ambiguous names ("Sudbury", "Harrow") return the candidates so Claude can ask which one you meant.

### Departure board

`show_board` comes with an [MCP App](https://modelcontextprotocol.io/specification/draft/extensions/apps): a client that supports MCP Apps draws its result as a split-flap departure board next to the answer. A client draws the board for every call of a tool that has one, so it is on a tool of its own, which the assistant is told to use only when someone asks to see a board. `live_departures` and `live_arrivals` return the same trains and draw nothing. A client without MCP Apps gets `show_board`'s result as it would theirs.

The board is one page, [`apps/rail/ui/board.html`](apps/rail/ui/board.html), served as the resource `ui://traintracker/board.html`. Its style and script are inline and it loads nothing from anywhere. The client hands it the tool's result; it shows up to 12 trains, the station's notices and the data credit. **Refresh** asks the client to call `show_board` again with the same arguments, which counts against the rate limit like any other call. The flaps don't turn for someone who has asked their system for reduced motion, and a screen reader gets the trains as a table.

## Run your own copy

Everything from here on is for running the server yourself. The public service at traintrackr.live is this code, deployed as described in [Deploy to Render](#deploy-to-render).

## Try it without accounts (demo mode)

Set `TRAINTRACKER_DEMO=1` to test every tool before your accounts are approved. The server then uses generated example data and makes no network requests:

- **Timetable:** a generated SCHEDULE feed for 25 real stations in East Anglia and London (Liverpool Street, Stratford, Chelmsford, Colchester, Ipswich, Norwich, Marks Tey, Sudbury, Cambridge, Kings Cross and others), running from today for 90 days. Sundays start later. It is stored in its own schema (`timetable_demo`), apart from the real timetable, and rebuilt each day. Demo mode still needs `DATABASE_URL`.
- **Live boards:** Darwin departure, arrival and service-details responses are generated from that timetable in-process. About one train in five runs late, one in 25 is cancelled and one in 12 has a platform change; the same train on the same day always gets the same result. Large stations (Liverpool Street, Kings Cross, Cambridge, …) announce platforms 15 minutes before departure, so the scheduled-platform fallback gets exercised.

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
| 4 | Rail Data Marketplace — *Live Arrival Board* product | Optional | Free | Darwin arrivals boards. Without it, arrivals show scheduled times. | `live_arrivals` |

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

The server downloads the feed in the background when the timetable is missing or older than 26 hours. It checks this on start-up and on every tool call, so a server left running for days stays current. If a download fails it keeps serving the old timetable, and `data_status` shows the error. You can also run `manage.py refresh_timetable --commit` by hand (see [Commands](#commands)). Set `TIMETABLE_AUTO_REFRESH=0` where a separate job runs `refresh_timetable`, as on Render.

### 3. Postgres

The timetable lives in one schema (`timetable` by default) of the database in `DATABASE_URL`. The role needs to create schemas in that database; the owner of the database can. On a shared server, give traintracker its own database and role:

```sql
CREATE ROLE traintracker LOGIN PASSWORD '…';
CREATE DATABASE traintracker OWNER traintracker;
REVOKE CONNECT ON DATABASE traintracker FROM PUBLIC;
```

Put that role in `DATABASE_URL`, not the server's own user. The server's user owns every database on it, so a fault in traintracker would reach the other applications' data; the `traintracker` role reaches its own database only.

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
    L1 -->|no| TT[(Timetable<br/>scheduled times, flagged)]

    T --> TT

    P --> CSA[Connection Scan<br/>over the day's timetable]
    CSA --> OV{Today, departing<br/>within 2 hours?}
    OV -->|yes| Darwin2[Darwin expected times<br/>+ tight-connection flag]
    Darwin2 -.->|offline| Note[Plan note: Darwin offline,<br/>times are scheduled]

    S --> ID{ID prefix}
    ID -->|darwin:| Darwin3[Darwin Service Details]
    ID -->|tt:| TT
```

| Source | Freshness | Coverage | Cached for | Limits to know |
|---|---|---|---|---|
| Darwin | Real time | Now → +2 hours | 20 s | Darwin service IDs expire soon after the train runs |
| Timetable (Postgres) | Daily (Network Rail publishes ~06:00) | Two days back → end of the published timetable (usually months) | Until the next rebuild | Scheduled times only; last-minute changes show up in Darwin, not here |

### Trainline links

A train from the timetable (`timetable`, `plan_journey`, `service_details` with a `tt:` ID, and the live tools when they fall back to scheduled times) carries `trainline_url`: that train's page on Trainline, with its stops and how it is running. The address is `<TRAINLINE_LIVE_URL>/departures/<station>/<token>`, where the token is the base64 of `/callingPattern/<train UID>/<run date>`. The station only titles the page: the board's station, the station a leg is boarded at, or the train's origin.

- Trains from Darwin have no link: a Darwin service ID carries no train UID.
- Trainline names stations by a slug of its own name for them. Most follow from the name in the station list; [`trainline_slugs.json`](apps/rail/data/trainline_slugs.json) holds the ones that differ, and `null` for stations Trainline has no live board for (mostly ones opened since 2022). A train that calls only at those has no link.
- Trainline publishes no specification for these addresses. They were read off its live boards, and the slugs checked against [its sitemap of live pages](https://www.thetrainline.com/live/sitemap), in September 2026. For a train it doesn't know, Trainline shows the station's board.
- Demo mode gives no links: its trains are made up.

### Darwin allowance

Free Darwin access covers 5 million requests per four-week railway period, and one server key serves every user. The server counts the requests it sends to Darwin and reports the total in `manage.py rail_status`, and in `data_status` when `DJANGO_DEBUG=1` (a hosted server doesn't tell everyone who signs in how much is left):

```
Darwin usage: 412,906 requests in the last 28 days, 8.26% of 5,000,000
```

- **What is counted:** each request sent to Darwin, failed ones included. A board answered from the 20-second cache sends nothing and counts nothing. Demo mode counts nothing.
- **The period:** a rolling 28 days. Railway periods are not simple to derive (the first and last of the year vary in length), and a rolling window is never less strict than the period it overlaps.
- **Per product:** the count is kept for each Rail Data Marketplace product (`departures`, `arrivals`, `service`) and the percentage uses the total. If your allowance is per product, the percentage overstates your usage.
- **Daily limit:** the server sends Darwin at most `DARWIN_DAILY_LIMIT` requests in one UK day (170,000 by default; the allowance works out at about 178,000 a day). Past that the live tools answer with scheduled times, and say why, until midnight. The count for the day is resumed after a restart. `0` turns the limit off.
- **Warnings:** the log gets a warning when usage passes 70% of the allowance and another at 90%. Each is given once, and again only if usage falls below the mark and returns, or the server restarts above it.
- **Storage:** one small table, `darwin_requests`, in its own schema (`USAGE_SCHEMA`), so the count survives restarts and timetable refreshes. Counts gather in memory and are written when 50 are waiting or a minute has passed, and at shutdown; a server that is killed loses at most that many. A failed write is logged and retried, and never fails a tool call. Rows older than 60 days are deleted.

### What the timetable keeps

The SCHEDULE feed is large (all trains, freight included). On import, traintracker keeps only what a passenger needs:

- Passenger categories only (trains, replacement/timetabled buses, ships). Freight and empty stock are dropped, except overlay and cancellation records, which can stop a passenger train running on a given day.
- Stops with a public time only; junctions and passing points are dropped.
- Schedules that ended more than two days ago are dropped.

The timetable therefore covers two days before it was built up to the last date any schedule runs, and a tool asked about a date outside that says so.

For each date it applies Network Rail's precedence rules per train: cancellation (C) beats new (N), which beats overlay (O), which beats permanent (P), and it honours each schedule's running days and date range. Bank-holiday running flags are not applied yet, so on bank holidays check the live board.

### Journey planning

`plan_journey` uses a round-based Connection Scan over every train hop of the day: round *n* finds the earliest arrival using at most *n* trains. That gives the fastest journey and the slower options with fewer changes in one pass, up to `max_changes`. It repeats from just after each departure to find the next few options, then drops any option that another beats on departure, arrival and changes at once, so you're never told to leave earlier than necessary. Results are ordered by arrival, and the fewest-changes option is always included.

- **Minimum change time:** 5 minutes between trains by default (`MIN_INTERCHANGE_MINUTES`). Walk/Tube links already include time to get in and out of stations, so no extra change time is added after them.
- **Cross-London:** the Tube isn't in the rail timetable, so transfers between London terminals (Kings Cross, Liverpool Street, Waterloo, etc.) are approximated: walks under 0.8 km at walking pace plus 5 minutes, otherwise Tube at ~12 minutes plus 3.5 minutes per km. They're labelled `tube (approx.)`.
- **Live overlay:** for today's legs departing within two hours, Darwin's expected times and platforms are added, and `connection_at_risk` is set if a delay or cancellation eats into a change, including one made via a walk/Tube link. If Darwin is down, the plan is still returned without live times.
- The search covers the service day (trains running into the early hours are included); it doesn't carry over to the next morning. Station boards do include trains just after midnight.
- **Load:** a plan scans the day's trains several times, and the network for a date takes seconds to build and over a hundred megabytes to hold. Two dates are kept in memory. Timetable reads and plans run in threads, two at a time; a call that waits more than 10 seconds for its turn is told the server is busy.

## Install

Requires [uv](https://docs.astral.sh/uv/), Python 3.14 (uv installs it from `.python-version`) and a Postgres database (any version from 13). Locally, Docker (or OrbStack) is the quickest way to get one:

```bash
docker run -d --name traintracker-pg --restart unless-stopped \
  -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:55432:5432 postgres:17
```

```bash
git clone git@github.com:hugorodgerbrown/traintracker.git
cd traintracker
uv sync
cp .env.example .env          # fill in your keys and DATABASE_URL (.env is git-ignored)
uv run python manage.py migrate
uv run python manage.py refresh_timetable --commit   # first timetable download (a few minutes)
uv run python manage.py rail_status
uv run python manage.py runserver                    # the site, sign-in and /mcp at localhost:8000
```

### Add to the Claude desktop app

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`. Use the full path to `uv` (run `which uv`), because the app doesn't use your shell's `PATH`:

```json
{
  "mcpServers": {
    "traintracker": {
      "command": "/Users/hugo/.local/bin/uv",
      "args": ["run", "--directory", "/Users/hugo/Projects/traintracker", "python", "manage.py", "serve_stdio"]
    }
  }
}
```

`--directory` makes the project folder the working directory, so the server reads your keys from `.env` there. `serve_stdio` answers only the app that started it, so it has no sign-in and no rate limit. Keys can go in an `"env"` block in this file instead; real environment variables take precedence over `.env`.

Restart the Claude app, then ask *"what's the status of traintracker's data sources?"* to check everything is connected.

## Configuration

All configuration is by environment variable, read from `.env` in the project folder if present. See [`.env.example`](.env.example). Local development needs none of the Django settings; on Render, titan's env groups set them (see [Deploy to Render](#deploy-to-render)).

The Django settings, in [`config/settings.py`](config/settings.py):

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `1` | `0` turns on the production hardening `manage.py check --deploy` asks for. With `1`, sign-in emails go to the console log and `data_status` shows the internal details |
| `DJANGO_SECRET_KEY` | a development key | Required with `DJANGO_DEBUG=0`. Also keys the hash of each sign-in code |
| `ACCOUNTS_EMAIL_KEY` | a fixed development key | Required with `DJANGO_DEBUG=0`. Keys the one-way hash each account is found by, so no email address is stored. Never change it: every account would be lost |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names the site answers; Render's `onrender.com` name is added for you |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Comma-separated origins trusted for form posts |
| `SITE_URL` | `http://localhost:8000` (on Render, the `onrender.com` name) | The public address: links in emails, the passkey origin, and the connector address the site shows (`<SITE_URL>/mcp`) |
| `RATE_LIMIT_PROXY_COUNT` | `0`, or `1` with `DJANGO_DEBUG=0` | Proxies in front of the app; the client address for the limits is that many hops from the end of `X-Forwarded-For` |
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `587` | SMTP relay for the sign-in email. For [Resend](https://resend.com): host `smtp.resend.com` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | — | SMTP login. For Resend: user `resend`, and the API key as the password |
| `EMAIL_USE_TLS` | `1` | STARTTLS to the relay |
| `DEFAULT_FROM_EMAIL` | `Traintrackr <noreply@localhost>` | Sender of the sign-in email; its domain must be verified with the relay |
| `WEBAUTHN_RP_ID` | host name of `SITE_URL` | Where passkeys work (see [docs/accounts.md](docs/accounts.md)) |
| `FIVEBAR_DOMAIN` | — | Turns on cookieless visitor counts by fivebar for that domain; off when empty |
| `FIVEBAR_TOKEN` | — | With `FIVEBAR_DOMAIN`, counts each MCP tool call in fivebar as `/mcp/<tool>`: the tool, whether it answered, the app's name and a keyed hash of the account, never the arguments or the answer ([the decision](docs/decisions/visitors-are-counted-by-fivebar.md)). A token made in fivebar with **Send events** only; off when empty |

The train tools' settings, read by [`apps/rail/config.py`](apps/rail/config.py):

| Variable | Default | Purpose |
|---|---|---|
| `DARWIN_API_KEY` | — | Live Departure Board consumer key |
| `DARWIN_DEPARTURES_URL` | RDM LDBWS URL | Override if your Specification tab differs |
| `DARWIN_SERVICE_API_KEY` / `DARWIN_SERVICE_URL` | board key / RDM URL | Service Details product |
| `DARWIN_ARRIVALS_API_KEY` / `DARWIN_ARRIVALS_URL` | — | Live Arrival Board product |
| `NR_USERNAME` / `NR_PASSWORD` | — | Network Rail data feeds login |
| `NR_SCHEDULE_URL` | full daily JSON extract | Override if the portal gives a different link |
| `DATABASE_URL` | — | Postgres database for the timetable (required), and for Django's own tables |
| `TIMETABLE_SCHEMA` | `timetable` | Schema the timetable lives in (demo mode appends `_demo`) |
| `USAGE_SCHEMA` | `traintracker_usage` | Schema for the count of requests sent to Darwin (see [Darwin allowance](#darwin-allowance)) |
| `DARWIN_DAILY_LIMIT` | `170000` | Most requests sent to Darwin in one UK day; past it the live tools answer with scheduled times. `0` turns the limit off |
| `TIMETABLE_AUTO_REFRESH` | `1` | `0` stops the server downloading the timetable itself (use a cron job instead) |
| `TIMETABLE_MAX_AGE_HOURS` | `26` | Re-download when older than this |
| `TRAINTRACKER_DATA_DIR` | `$XDG_DATA_HOME/traintracker` if set, else `~/.traintracker` | Where the feed is downloaded to before import |
| `RATE_LIMIT_PER_MINUTE` | `30` | Tool calls per account per minute on `/mcp`; `0` turns the limit off |
| `RATE_LIMIT_BURST` | `10` | Tool calls an account can make at once before the per-minute rate applies |
| `OPENAI_APPS_CHALLENGE` | — | Token from OpenAI's plugin portal. When set, it is returned at `/.well-known/openai-apps-challenge`, which OpenAI fetches to verify the domain |
| `MIN_INTERCHANGE_MINUTES` | `5` | Minimum change time for planning |
| `TRAINLINE_LIVE_URL` | `https://www.thetrainline.com/live` | Base of the [Trainline link](#trainline-links) on timetable services |
| `HTTP_TIMEOUT_SECONDS` | `15` | Upstream request timeout |
| `TRAINTRACKER_DEMO` | off | `1` uses generated example data instead of any account (see [demo mode](#try-it-without-accounts-demo-mode)) |
| `TRAINTRACKER_NO_DOTENV` | off | `1` stops the train tools reading `.env` (the [preview servers](#preview-servers) set it) |

## Commands

Every command is a Django management command, run as `uv run python manage.py <command>`. The ones that change data only say what they would do unless given `--commit`.

| Command | Does |
|---|---|
| `runserver` | Run the site, sign-in and `/mcp` locally (production runs gunicorn; see [Deploy to Render](#deploy-to-render)) |
| `serve_stdio` | Run the MCP server on stdio (what the Claude desktop app and the MCP Inspector run). No sign-in and no rate limit |
| `migrate` | Create or update Django's tables |
| `refresh_timetable [--commit]` | Download the SCHEDULE feed and rebuild the timetable (in demo mode, rebuild the generated timetable) |
| `import_timetable FILE [--commit]` | Build the timetable from a feed file you downloaded yourself (JSON, gzipped or not) |
| `rail_status` | Show configured sources, Darwin allowance used and timetable details |
| `forget_account EMAIL [--commit]` | Delete the account for an email address, with its passkeys, its saved boards, its connected apps' tokens and any sign-in request (an erasure request) |
| `block_account EMAIL [--commit]` | Close the account for an address, or make a closed one, so it can't sign in and its connected apps stop |
| `purge_idle_accounts [--commit]` | Delete accounts with no sign-in and no new token for 180 days |
| `purge_sign_in_requests [--commit]` | Delete sign-in requests past their expiry |
| `purge_idempotency_records [--commit]` | Delete idempotency records past their 24-hour window (Titan's) |
| `cleartokens` | Delete expired OAuth tokens (django-oauth-toolkit's) |

The account commands are described in [docs/accounts.md](docs/accounts.md). Under `serve_stdio`, logs go to stderr and stdout carries the MCP protocol.

## Deploy to Render

[`render.yaml`](render.yaml) defines three services in Frankfurt:

| Service | Type | Does | Environment |
|---|---|---|---|
| `traintracker` | Web service, 512 MB, [traintrackr.live](https://traintrackr.live) | gunicorn, one worker with 8 threads. The build runs `collectstatic`, `migrate` runs before each deploy goes live, and the health check is `/livez` | Env groups `titan` and `traintracker`; `DARWIN_API_KEY`, `OPENAI_APPS_CHALLENGE` and `TIMETABLE_AUTO_REFRESH=0` on the service |
| `traintracker-refresh` | Cron job, 06:30 UTC daily | `manage.py refresh_timetable --commit` | Env groups `titan` and `traintracker`; `NR_USERNAME`, `NR_PASSWORD` on the service |
| `traintracker-purge` | Cron job, 03:17 UTC daily | `purge_idempotency_records`, `purge_sign_in_requests`, `cleartokens` and `purge_idle_accounts`: what the privacy policy says is deleted | Env groups `titan` and `traintracker` |

The two env groups are made by titan, the command-line tool that comes with [django-titan-template](https://github.com/hugorodgerbrown/django-titan-template). `titan` is shared by every Titan app (debug off, the Python version, hosts and origins, the SMTP relay); `traintracker` is this app's own (`DATABASE_URL`, `DJANGO_SECRET_KEY`, `ACCOUNTS_EMAIL_KEY`, the mail key and sender, `SITE_URL`, `FIVEBAR_DOMAIN` and, added by hand, `FIVEBAR_TOKEN`). `ACCOUNTS_EMAIL_KEY` makes the one-way hash each account is found by: keep a copy, and never change it ([docs/accounts.md](docs/accounts.md)). The Blueprint refers to them without declaring them, so a sync never overwrites them; a value set on a service wins over its groups'. The web service runs one worker because the train tools share one event loop, one Darwin budget and one journey network per process ([the decision](docs/decisions/the-train-tools-run-on-one-event-loop.md)). It holds one day's journey network in memory for `plan_journey` (about 200 MB), so it needs at least 512 MB.

1. Run `titan deploy traintracker`. It makes what the Blueprint needs before the first deploy: the shared `titan` group, the app's role and database on the shared Postgres instance, a sending-only Resend key, and the `traintracker` group. Then it deploys the Blueprint, adds the domain `traintracker.<parent domain>` to the service, and checks `/healthz` on its `onrender.com` name. Set `DARWIN_API_KEY`, `OPENAI_APPS_CHALLENGE`, `NR_USERNAME` and `NR_PASSWORD` on their services when Render asks for them.
2. Run the `traintracker-refresh` cron job once by hand (**Trigger Run**) to load the first timetable.
3. Put the service on its domain. titan's own `golive` points an app at `<slug>.<parent domain>`; this deployment keeps `traintrackr.live`, which the directory listings, the MCP Registry and people's connectors use, so it is done by hand: add the domain to the service, add it to the service's `DJANGO_ALLOWED_HOSTS` (after the `titan` group's value), and set `SITE_URL=https://traintrackr.live` and `FIVEBAR_DOMAIN=traintrackr.live` in the `traintracker` group. titan never overwrites a value that is already set, so `DEFAULT_FROM_EMAIL` can be changed there too.
4. Add the server as a claude.ai connector (below).

### Add as a claude.ai connector

`/mcp` is Titan's MCP view behind mcp-auth, the template's OAuth server on [django-oauth-toolkit](https://github.com/jazzband/django-oauth-toolkit), so it can be added once in claude.ai and used from claude.ai, Claude Desktop and the mobile apps. ChatGPT connects the same way.

1. In claude.ai: **Settings → Connectors → Add custom connector**, name `traintracker`, URL `https://<your-service-host>/mcp`. Leave the OAuth client fields empty: Claude registers itself.
2. Claude opens the server's sign-in page. Sign in (see [Sign-in](#sign-in)).

Signing in hands Claude a one-hour access token and a 30-day refresh token, rotated on each refresh. The `offline_access` scope is listed, which ChatGPT needs to stay connected when its access token expires. Only callbacks to Claude, ChatGPT and loopback addresses (desktop clients such as Claude Code) are accepted (`MCP_AUTH` in [`config/settings.py`](config/settings.py)). Anyone with an active account may connect. `/oauth/connected/` lists the apps connected to the signed-in account. The `traintracker-purge` cron job deletes expired tokens each night.

### Sign-in

Sign-in is Titan's accounts: an email address, then the link or the six-digit code in the email it sends, or a passkey added from the account page. There are no passwords. [docs/accounts.md](docs/accounts.md) has the details: how long a link and a code last, the limits on emails and code tries, what an account keeps, and how to close or forget one. With `DJANGO_DEBUG=1` the sign-in email is written to the console log in place of being sent; don't use that on a host whose logs other people can read.

**Rate limit.** Tool calls on `/mcp` are limited per account: `RATE_LIMIT_BURST` calls at once, refilled at `RATE_LIMIT_PER_MINUTE`. One Darwin key serves every user, and the limit stops one account spending the whole allowance. A call pays for one request to Darwin; a call that makes more (a long board read page by page, a journey with several legs) is charged one for each further request, so the account's next calls wait longer. A journey plan counts as three calls. A call over the limit comes back as a tool error, *Too many requests. Try again in N seconds.*, which the model can read and relay. The counts are held in memory, so a restart clears them. `serve_stdio` is not limited.

## Site

The Django app also serves a small public site, so there is one deploy and one domain:

| Path | Page |
|---|---|
| `/` | What the server does, the connector address with a copy button, how to add it to Claude and ChatGPT, three example prompts |
| `/docs` | Each tool in plain English, three worked examples with what the answer contains, the limits, data sources, support |
| `/privacy` | Privacy policy (UK GDPR): what is processed, why, for how long, and by whom |
| `/terms` | Terms of use: the service as is, accuracy, liability, fair use, data licences, governing law |
| `/board` | Pick a station, and a platform if you like, for a live board (see [Shareable boards](#shareable-boards)) |

The site is [`apps/public/`](apps/public). The pages are Django templates: each page's content is a fragment in `templates/public/pages/` (`index.html`, `docs.html`, `privacy.html`, `terms.html`) placed inside `templates/public/layout.html`, rendered by [`views.py`](apps/public/views.py); [`pages.py`](apps/public/pages.py) lists them, builds the footer's links, and turns the privacy page into the text the `privacy_policy` tool returns. The shareable boards (the board pages, the picker, the embed, the TV view, the JSON feed and the station suggestions) are rendered per request by [`boards.py`](apps/public/boards.py); `frame.html` is the board alone, for an embed or a TV. The static files are in [`static/`](static): one stylesheet, `css/site.css`, and two scripts: `js/site.js`, for the copy buttons, the departure boards and the station picker, and `js/saved_boards.js`, for your last board and favourites. The board is the app's look, drawn by `site.js` from a table of example trains in `index.html`: `apps/rail/ui/board.html` keeps its style and script inline, which the site's content security policy refuses, so the flaps are written out again in `site.css` and `site.js`. A change to how the app draws its flaps goes in both. The icon is `static/icons/icon.svg`, with PNGs rendered from it at 32, 180 and 512 pixels; `icon-512.png` is the one uploaded to the directory listings, and `/favicon.ico` redirects to the 32-pixel PNG. To change the icon, edit the SVG and render the PNGs again, for example `magick -background none -density 1200 icon.svg -resize 512x512 PNG32:icon-512.png`. The link-preview card is `static/img/share.png`, 1200 by 630, drawn from `static/img/share.svg`: the front page's promise beside the departure board app. It needs a browser to render: the card uses the system's sans-serif and bold monospace fonts, which ImageMagick's own SVG renderer can't select. Draw `share.svg` onto a 1200 by 630 canvas in a browser and save that as a PNG, or take a screenshot of it at that size with a device pixel ratio of 1; then `magick saved.png -strip PNG24:share.png` drops the alpha channel, which the tests reject. In `share.svg` each flap is a `<rect>` and each letter its own `<text>`, so changing a train means changing its letters one by one. There is no build step. The pages name the server's own values (the connector address from `SITE_URL`, the fair-use line from `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_BURST`), so a copy deployed elsewhere describes itself. The privacy policy and terms name this repository's deployment and its operator: change `privacy.html`, `terms.html` and the support address when you deploy your own.

The stylesheet and script are served by WhiteNoise under names that carry a hash of the file (`collectstatic`), so a browser fetches a changed file at once and can cache an unchanged one. The pages set no cookies and load nothing from another origin, except fivebar's counter while `FIVEBAR_DOMAIN` is set; the `Content-Security-Policy` header allows only the site's own stylesheet, script and fetches. They follow the reader's light or dark setting and work at phone width.

### Shareable boards

`/board/LST` is a station's live departures as a page of its own, `/board/LST/9` one platform's, and `/board/LST/COL` the trains calling at Colchester (or, to a station with no direct train in the next two hours such as `/board/LST/SUY`, each train with its connection at the station it changes at): the homepage's split-flap board with live trains and a station clock, to keep open on a screen, share as a link or embed in another site. Nobody has to sign in. `/board` is a form to pick the station, and a destination or a platform; the station fields suggest names as you type.

| Address | What it is |
|---|---|
| `/board/LST` | The next 10 departures from London Liverpool Street: live for the next two hours, then scheduled trains from the timetable where the board is short (see below) |
| `/board/LST/9` | Departures from platform 9 only; a train whose live platform isn't announced yet is matched on its scheduled one |
| `/board/LST/COL` | Departures calling at Colchester, wherever they end: the Norwich trains too |
| `/board/LST/COL?tv=1` | The board alone, filling a TV screen: see below |
| `/board/LST/9?embed=1` | The same board alone, with no site header or footer, for an `<iframe>`. It is the only page another site may frame |
| `/api/board/LST/9` | The board as JSON, which the page fetches every minute while it is visible |
| `/api/boards` | Your saved boards, signed in: see below |

Darwin answers for two hours, so a quiet line, or any station overnight, would leave a board nearly empty. A board with fewer than its 10 trains goes on with scheduled trains from the timetable, from where Darwin's two hours end and up to a day ahead, so a train is never on it twice. Each says **Scheduled** in Expected, in pale letters (amber stays live and on time), and turns to its live time once it is within two hours. A train on a later day comes under that day's name (MONDAY 19 OCTOBER), and the credit names both sources. The tools mark these trains `source: "timetable"`, with the `date` they run on.

After the station, a station's three-letter code is a destination and anything else a platform. A board is for a destination or a platform, never both. Each board has one address: a lower-case code, a name (`/board/cambridge`, `/board/LST/colchester`) or a platform written `p9` or `platform-9` redirects to it, so every share of a board is the same link. A name that fits several stations goes to the picker, which asks which. The board page has the link and the `<iframe>` code, each with a copy button, and a link to each platform's board.

The page is rendered with the trains in it, as a table, so it works without JavaScript; `site.js` draws the flaps, which turn from the old letters to the new at each update. Because anyone can open a board, and a board embedded in a busy page is opened by all its visitors, what reaches Darwin is one fetch per board a minute, not one per viewer: an answer (or a failure) is kept for 60 seconds and shared by every request for that board (`SharedBoards` in `board.py`), and requests that arrive while it is being fetched wait for the same answer. Each client address may open 30 boards at once and 60 a minute (`BOARD_LIMIT` in [`apps/public/board.py`](apps/public/board.py)); over that the answer is 429 with `Retry-After`. The boards count toward `DARWIN_DAILY_LIMIT` like any other request.

**Your boards.** The front page shows the last board you opened, live, in place of its example board, and your favourites under it as links; a board page has **Add to favourites** (up to 12, the oldest going when you add a 13th). Signed out, the browser keeps them (`localStorage`) and nothing is sent to the server. Signed in, the account keeps them (`SavedBoard`, `/api/boards` in [`apps/public/saved.py`](apps/public/saved.py)) and the browser keeps nothing, so they are the same on every device; signing in moves what that browser kept into the account. Embeds and TV views are never remembered. The front page's HTML is the same for everyone, so it stays cached: `static/js/saved_boards.js` and `site.js` fill in your boards. Why: [docs/decisions/saved-boards-live-in-the-browser-or-the-account.md](docs/decisions/saved-boards-live-in-the-browser-or-the-account.md).

**On a TV.** `?tv=1` shows the board alone and sizes its flaps so that every train fits the screen, in one line a train on a wide screen and two on a tall one; the columns the screen has left over at that size widen the destination, so the board fills the width and long names fit. On the board page, **Show on TV** switches to the same view in place and goes full screen, for a window dragged onto a TV used as an extended display or a cast tab; leaving full screen goes back. On a TV the screen is kept awake (Screen Wake Lock, where the browser has it), the cursor hides after 3 seconds without moving, and the page reloads itself once a night, between 03:10 and 03:50 UK time, so a board left running for weeks picks up changes to the site. It shares its fetch with every other view of that board, so it costs Darwin nothing extra. Every live board counts down to its next update beside "Updated", and one that hasn't updated for 5 minutes dims and says when it last did.

## MCP Registry

[`server.json`](server.json) describes the server for the official [MCP Registry](https://registry.modelcontextprotocol.io): the name `live.traintrackr/traintracker`, the version, and one remote, streamable HTTP at `https://traintrackr.live/mcp`. It follows the registry's `2025-12-11` schema. A test keeps its version in step with `pyproject.toml`.

Version 0.1.0 is published. CI doesn't publish: a new version is published by hand, with `mcp-publisher login dns` and then `mcp-publisher publish`. A `live.traintrackr/` name needs proof that you hold the domain, which is a TXT record on the apex of `traintrackr.live` carrying the public half of the signing key. For your own deployment, change the name and the URL to your domain.

## Limitations

- **Past running times** ("was the 08:00 late yesterday?") aren't available. Darwin only covers now → +2 hours, and the timetable is scheduled times.
- **Darwin outages**: when Darwin is offline, the live tools show scheduled times and say that Darwin is offline, so delays, cancellations and live platforms are missing until it returns.
- **Fares** aren't included.
- **Tube/bus/tram** aren't in the timetable beyond the approximate London terminal links.
- **Engineering works** appear in the timetable once Network Rail publishes them (usually well ahead), and in Darwin on the day.

## Development

Traintrackr is a Django project on [Titan](https://github.com/hugorodgerbrown/django-titan-template), the project template its sibling apps are built from. It moved onto Titan in four steps, from a Starlette app of its own ([the decision](docs/decisions/traintrackr-moves-onto-titan-in-four-steps.md)); keep new work close to the template so `copier update --trust` stays a clean merge. HTTP is served by Django under gunicorn, with the train tools on one event loop per process ([the decision](docs/decisions/the-train-tools-run-on-one-event-loop.md)).

tox runs every check on Python 3.14 from `uv.lock` (via the `tox-uv` plugin), as Titan does; [docs/testing.md](docs/testing.md) says which test goes where. The rail tests (`tests/rail/`) need a Postgres they can create schemas in, at `TEST_DATABASE_URL` (default `postgresql://postgres:postgres@127.0.0.1:55432/postgres`, which the `docker run` under [Install](#install) provides). Each test works in its own schema and drops it afterwards (`tests/rail/conftest.py`). CI runs Postgres 17 as a service.

```bash
uvx --with tox-uv tox              # fmt, lint, types, checks, test, audit
uvx --with tox-uv tox -e test      # one environment
uvx --with tox-uv tox -e test -- -k platform   # arguments after -- go to the tool
uvx --with tox-uv tox -e sast      # semgrep (downloads its rule packs)
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs each environment as its own job, with `sast` and a gitleaks scan as well, using the versions of uv, tox and tox-uv written into the workflow, and its Actions pinned to a commit. Dependabot ([`.github/dependabot.yml`](.github/dependabot.yml)) proposes updates to `uv.lock`, `package-lock.json` and the Actions each week; the uv and tox versions are raised by hand, uv in `render.yaml` as well. `uv run pre-commit install` runs ruff, gitleaks and mypy before each commit.

| Environment | Runs |
|---|---|
| `fmt` | `ruff format --check` |
| `lint` | `ruff check`: Titan's rules, docstrings and bandit included, everywhere. The SQL in `apps/rail/` (and its tests) is built with f-strings around schema names, so it is exempt from `S608` |
| `types` | `mypy` with django-stubs; `apps/rail/` stays strict |
| `checks` | `manage.py check --deploy` with production settings, and no missing migrations |
| `test` | `pytest` with coverage (floor 90%): `tests/rail/` for the importer, STP rules, planner, download, clients, demo mode, the tools, the commands, rate limit and Darwin usage; `tests/public/` for the site and the boards; `tests/mcp/` for the `/mcp` endpoint and its tools; `tests/accounts/` for sign-in, passkeys and the account commands; `tests/core/` for Titan's shared pieces |
| `js` | Vitest over `tests/js/` (jsdom): what `static/js` does in a page. Node is for the tests only (`package.json`) |
| `audit` | `pip-audit` over the production dependencies, and `npm audit` over the test tooling |
| `sast` | `semgrep` (not in the default list: it downloads rule packs) |

Tests use a synthetic SCHEDULE feed in Network Rail's JSON format (`tests/rail/feedgen.py`) and API fixtures shaped on the published Darwin schema. They aren't live recordings, so the first run against real services is the final check.

### Preview servers

[`.claude/launch.json`](.claude/launch.json) has two servers that Claude Code's preview pane can start. Both run in demo mode against the development Postgres above, ignore `.env`, and listen on `127.0.0.1` only. Set `PREVIEW_DATABASE_URL` to use another database.

| Name | Runs | Use it for |
|---|---|---|
| `web` | `manage.py migrate`, then `manage.py runserver` with `DJANGO_DEBUG=1` on port 8000, or a free port if that is taken | The public site, the sign-in pages and `/mcp`. Sign-in emails are written to the server's console log |
| `mcp-inspector` | The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) (version 2, through `npx`) on port 6274, with `manage.py serve_stdio` as its stdio target | Calling the tools by hand. Its Apps tab draws a tool's MCP App, where it has one |

The Inspector needs Node 22.19 or later. It starts a local server that can run commands, guarded by a token it makes at each launch and puts into the page it serves; the config leaves that on.

```
manage.py
config/          Settings (one module, from the environment), URLs, WSGI/ASGI
apps/
  core/          Titan's shared pieces: BaseModel, idempotency, rate limits, /livez and /healthz
  accounts/      Sign-in by email link or code, passkeys, the account commands
  mcp/           /mcp: Titan's MCP view (mcp-auth), the tool list, ui:// resources
  public/        The site: pages (views.py, pages.py), boards (boards.py, board.py), saved boards (saved.py), templates
  pwa/           The app's name and identity, as Titan's templates use it
  rail/          The train tools and the data behind them
    server.py    MCP tools, source fallback, live overlay
    runtime.py   The tools' event loop, one per process
    timetable.py SCHEDULE importer and Postgres queries (STP resolution)
    planner.py   Connection Scan journey planner, London links
    darwin.py    Rail Data Marketplace LDBWS client
    usage.py     Count of requests sent to Darwin, against the allowance
    ratelimit.py Per-account limit on tool calls
    stations.py  Station search and name resolution
    trainline.py Links to a train's page on Trainline
    schemas.py   Output models shared by all sources
    demo.py      Demo mode: generated timetable and in-process Darwin
    config.py    The train tools' settings, from the environment
    ui/          MCP App: the departure board (ui://traintracker/board.html)
    data/        stations.json, trainline_slugs.json
    management/  serve_stdio, refresh_timetable, import_timetable, rail_status
templates/       Traintrackr's sign-in, account and connect pages
static/          site.css, site.js, the sign-in scripts, the icons and the share card
tests/           core, accounts, mcp, public, rail
docs/            accounts.md, testing.md, decisions/
```

## Data and licences

Darwin data via the Rail Data Marketplace, and Network Rail data feeds, are used under the terms you accept when you subscribe. Check those terms before redistributing any output. Both ask for the source to be credited:

| Data | Credit | Where the wording comes from |
|---|---|---|
| Darwin (live times) | "Powered by National Rail Enquiries", with a link to [nationalrail.co.uk](https://www.nationalrail.co.uk/) and the logo from the NRE Brand Guidelines | [NRE Developer Guidelines](https://www.nationalrail.co.uk/developers/darwin-data-feeds/) v06-01, section 4. Where the feed is combined with other data, the credit may go on an attribution page |
| Network Rail SCHEDULE (timetable) | "Contains public sector information licensed under the Open Government Licence v3.0." | The [Network Rail data feeds licence](https://www.networkrail.co.uk/who-we-are/transparency-and-ethics/transparency/open-data-feeds/network-rail-infrastructure-limited-data-feeds-licence/) releases the feeds under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) and gives no statement of its own, so the licence's default applies |
| Station list | [davwheat/uk-railway-stations](https://github.com/davwheat/uk-railway-stations), Open Database License (ODbL) | The repository's licence |

The credits are on `/docs`, under "Where the data comes from". The footer of every page of the [site](#site) carries the National Rail Enquiries credit and a "Data sources" link to that section. The server's instructions carry one line naming the sources, so an assistant can credit them when it says where an answer comes from; tool responses are not padded with it. The footer has the words and the link but not the NRE logo: the brand pack on the Darwin data feeds page makes the logo's use subject to National Rail's permission, which was asked for on 2026-09-30 (`TODO(hugo)` in `layout.html`).

Code: MIT.
