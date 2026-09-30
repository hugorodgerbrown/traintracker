# Traintrackr

An MCP server that lets Claude and ChatGPT answer questions about GB (National Rail) trains: *"when's the next train to Sudbury?"*, *"is the 13:00 from Liverpool Street on time?"*, *"how do I get from Cambridge to Sudbury next Saturday morning?"*

The public service runs at [traintrackr.live](https://traintrackr.live). It is free, and you sign in with an email address; there is no password. The connector address is:

```
https://traintrackr.live/mcp
```

## Use it

**Claude.** In claude.ai, open **Settings → Connectors → Add custom connector**. Name it *Traintrackr*, paste the connector address, and leave the OAuth fields empty. Choose **Connect**: Claude opens the Traintrackr sign-in page, where you enter your email address and then the six-digit code sent to it. A connector added in claude.ai also works in Claude Desktop and the mobile apps.

**ChatGPT.** Traintrackr isn't in the ChatGPT directory yet. Until it is, ChatGPT can add it only in Business, Enterprise and Edu workspaces, using developer mode on the web, which a workspace admin has to allow. Turn on **Settings → Apps → Advanced settings → Developer mode**, then in **Settings → Apps** choose **Create**, paste the connector address and choose OAuth. Sign in with your email address as above.

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
| `live_departures` | Next trains, expected times, platforms, delays, cancellations | Darwin; if it's not set up, booked times; if it's offline, booked times with a note saying so |
| `departure_platform` | "Which platform is the 13:00 to Colchester?" One train's platform, flagged `live` or `booked` | Darwin (paged); booked platform from the timetable until the live one is announced |
| `platform_departures` | "What are the next three trains from platform 7?" | Same as `departure_platform` |
| `live_arrivals` | Trains arriving in the next ~2 hours | Darwin arrivals; if not set up, booked times; if offline, booked times with a note saying so |
| `timetable` | Booked departures/arrivals at a station on any date | Local timetable |
| `service_details` | Every stop for one train | Whichever source issued the ID |
| `plan_journey` | A to B with changes (up to `max_changes`, default 4), incl. cross-London links | Local timetable + Darwin live overlay |
| `data_status` | What's configured, timetable freshness, what's missing; on a server you run yourself, Darwin allowance used | — |
| `privacy_policy` | "What do you keep about me?" The privacy policy as text, with the page's address | The `/privacy` page |

Every tool accepts station names or CRS codes. Ambiguous names ("Sudbury", "Harrow") return the candidates so Claude can ask which one you meant.

## Run your own copy

Everything from here on is for running the server yourself. The public service at traintrackr.live is this code, deployed as described in [Deploy to Render](#deploy-to-render).

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

### Darwin allowance

Free Darwin access covers 5 million requests per four-week railway period, and one server key serves every user. The server counts the requests it sends to Darwin and reports the total in `traintracker status`, and in `data_status` on a server you run over stdio (a hosted server doesn't tell everyone who signs in how much is left):

```
Darwin usage: 412,906 requests in the last 28 days, 8.26% of 5,000,000
```

- **What is counted:** each request sent to Darwin, failed ones included. A board answered from the 20-second cache sends nothing and counts nothing. Demo mode counts nothing.
- **The period:** a rolling 28 days. Railway periods are not simple to derive (the first and last of the year vary in length), and a rolling window is never less strict than the period it overlaps.
- **Per product:** the count is kept for each Rail Data Marketplace product (`departures`, `arrivals`, `service`) and the percentage uses the total. If your allowance is per product, the percentage overstates your usage.
- **Daily limit:** the server sends Darwin at most `DARWIN_DAILY_LIMIT` requests in one UK day (170,000 by default; the allowance works out at about 178,000 a day). Past that the live tools answer with booked times, and say why, until midnight. The count for the day is resumed after a restart. `0` turns the limit off.
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
| `USAGE_SCHEMA` | `traintracker_usage` | Schema for the count of requests sent to Darwin (see [Darwin allowance](#darwin-allowance)) |
| `DARWIN_DAILY_LIMIT` | `170000` | Most requests sent to Darwin in one UK day; past it the live tools answer with booked times. `0` turns the limit off |
| `TIMETABLE_AUTO_REFRESH` | `1` | `0` stops the server downloading the timetable itself (use a cron job instead) |
| `TIMETABLE_MAX_AGE_HOURS` | `26` | Re-download when older than this |
| `TRAINTRACKER_DATA_DIR` | `$XDG_DATA_HOME/traintracker` if set, else `~/.traintracker` | Where the feed is downloaded to before import |
| `MCP_AUTH_TOKEN` | — | Static bearer token accepted by `serve-http` |
| `MCP_OAUTH_PASSPHRASE` | — | Passphrase accepted by the OAuth sign-in page, for the owner and for directory reviewers. `serve-http` needs this, email sign-in, `MCP_AUTH_TOKEN`, or any mix of them |
| `RESEND_API_KEY` | — | [Resend](https://resend.com) API key. With `MAIL_FROM` and `MCP_ACCOUNT_SECRET` it turns on email sign-in (see [Sign-in](#sign-in)) |
| `MAIL_FROM` | — | Sender of the sign-in code, e.g. `Traintrackr <login@mail.traintrackr.live>`; the domain (here the subdomain `mail.traintrackr.live`) must be verified with Resend |
| `MCP_ACCOUNT_SECRET` | — | Key that turns an email address into an account ID. Long and random; changing it gives every address a new account |
| `MAIL_BACKEND` | `resend` | `console` writes the sign-in code to the log instead of sending it. For local development only |
| `MAIL_MAX_PER_HOUR` | `100` | Most sign-in codes sent in an hour, over all addresses |
| `RATE_LIMIT_PER_MINUTE` | `30` | Tool calls per account per minute over HTTP; `0` turns the limit off |
| `RATE_LIMIT_BURST` | `10` | Tool calls an account can make at once before the per-minute rate applies |
| `MCP_REDIRECT_HOSTS` | `claude.ai,claude.com,chatgpt.com,platform.openai.com` | Hosts a client may return the browser to after sign-in, over https. Loopback addresses are always allowed. `*` allows any address |
| `CLIENT_IP_HEADER` | — | Header the hosting platform's proxy puts the caller's address in (`CF-Connecting-IP` on Render), used for the limits before sign-in. Leave unset where clients can reach the server without passing that proxy: the connection's own address is used |
| `MCP_PUBLIC_URL` | `https://` + first public host | Base URL clients use; the OAuth issuer and resource (`<url>/mcp`) derive from it |
| `MCP_AUTH_SCHEMA` | `mcp_auth` | Schema for OAuth clients, codes, tokens and accounts (tokens stored as SHA-256 hashes, accounts as keyed hashes of the address) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Where `serve-http` listens (bind address) |
| `MCP_PUBLIC_HOSTS` | — | Comma-separated extra hostnames clients use (e.g. a custom domain), added to `RENDER_EXTERNAL_HOSTNAME`; `serve-http` rejects other `Host` headers (DNS-rebinding protection) |
| `OPENAI_APPS_CHALLENGE` | — | Token from OpenAI's plugin portal. When set, `serve-http` returns it at `/.well-known/openai-apps-challenge`, which OpenAI fetches to verify the domain |
| `MIN_INTERCHANGE_MINUTES` | `5` | Minimum change time for planning |
| `HTTP_TIMEOUT_SECONDS` | `15` | Upstream request timeout |
| `TRAINTRACKER_DEMO` | off | `1` uses generated example data instead of any account (see [demo mode](#try-it-without-accounts-demo-mode)) |

## Commands

| Command | Does |
|---|---|
| `traintracker` | Run the MCP server on stdio (what Claude runs) |
| `traintracker serve-http` | Run the MCP server over streamable HTTP at `/mcp`, behind OAuth sign-in and/or a static bearer token, with a `/healthz` check and the [public site](#site) |
| `traintracker forget EMAIL` | Delete the account for an email address, with its tokens (an erasure request) |
| `traintracker block EMAIL` | Stop an address signing in, and its tokens working |
| `traintracker refresh` | Download the SCHEDULE feed and rebuild the timetable (in demo mode, regenerate the demo timetable) |
| `traintracker import FILE.json.gz` | Build the timetable from a feed file you downloaded yourself |
| `traintracker status` | Show configured sources, Darwin allowance used and timetable details |

Logs go to stderr; stdout carries the MCP protocol.

## Deploy to Render

[`render.yaml`](render.yaml) defines two services in Frankfurt:

| Service | Type | Does | Environment |
|---|---|---|---|
| `traintracker` | Web service, 512 MB, [traintrackr.live](https://traintrackr.live) | `serve-http`; health check `/healthz` | `DATABASE_URL`, `DARWIN_API_KEY`, `MCP_OAUTH_PASSPHRASE`, `RESEND_API_KEY`, `MAIL_FROM`, `MCP_ACCOUNT_SECRET`, `MCP_PUBLIC_HOSTS`, `CLIENT_IP_HEADER`, `TIMETABLE_AUTO_REFRESH=0` |
| `traintracker-refresh` | Cron job, 06:30 UTC daily | `traintracker refresh` | `DATABASE_URL`, `NR_USERNAME`, `NR_PASSWORD` |

The web service holds one day's journey network in memory for `plan_journey` (about 200 MB), so it needs at least 512 MB.

1. Create the `traintracker` database and role on your Postgres instance (see [Postgres](#3-postgres)).
2. In the Render dashboard: **New → Blueprint**, pick this repository, and enter the values Render prompts for. The three email sign-in values go together: give all of them, or leave all three empty and sign in with the passphrase. Use the Postgres instance's *internal* URL, with the `traintracker` role and its password in place of the instance's own user, and `/traintracker` as the database name.
3. Run the cron job once by hand (**Trigger Run**) to load the first timetable.
4. Add the server as a claude.ai connector (below). Use your own service's host: its `onrender.com` name, or a custom domain you have added to the service and listed in `MCP_PUBLIC_HOSTS` (the `render.yaml` value is this repository's deployment, `traintrackr.live`). `render.yaml` turns the `onrender.com` name off (`renderSubdomainPolicy: disabled`), which Render allows only for a service with a custom domain: remove that line to use the `onrender.com` name.

For a client that can't do OAuth, set `MCP_AUTH_TOKEN` on the service to a long random value; it is accepted as a bearer token alongside OAuth, and never expires. For example, in a terminal (so the token stays out of any transcript), with the token on the clipboard:

```bash
claude mcp add -s user --transport http traintracker https://<your-service-host>/mcp --header "Authorization: Bearer $(pbpaste)"
```

### Add as a claude.ai connector

The server is its own OAuth authorization server, so it can be added once in claude.ai and used from claude.ai, Claude Desktop and the mobile apps. ChatGPT connects the same way.

1. In claude.ai: **Settings → Connectors → Add custom connector**, name `traintracker`, URL `https://<your-service-host>/mcp`. Leave the OAuth client fields empty: Claude registers itself.
2. Claude opens the server's sign-in page. Sign in (see [Sign-in](#sign-in)).

Sign-in hands Claude a one-hour access token and a 90-day refresh token, rotated on each refresh. A refresh token that turns up again more than a minute after it was replaced means two parties hold it, and that connection is signed out. The authorization server metadata lists the `offline_access` scope and public clients (`none`), which the MCP SDK's own metadata leaves out: ChatGPT may drop a connection when its access token expires unless `offline_access` is listed. Every client may ask for that scope; a refresh token is issued either way. Clients, codes, tokens and accounts are stored in the `mcp_auth` schema, tokens as SHA-256 hashes. The server keeps up to four database connections open for sign-in and token checks. To sign every client out, run `TRUNCATE mcp_auth.tokens` against the database. Changing the passphrase doesn't sign anyone out; truncate the tokens as well.

### Sign-in

`/mcp` always needs a bearer token. The sign-in page offers up to two ways to get one, depending on what is configured:

| Way in | For | Turned on by |
|---|---|---|
| A six-digit code sent by email | Anyone | `RESEND_API_KEY`, `MAIL_FROM` and `MCP_ACCOUNT_SECRET` |
| The passphrase | The owner, and directory reviewers, who need credentials that work without a mailbox | `MCP_OAUTH_PASSPHRASE` |

With only the passphrase set, the page is the passphrase form and nothing else. With both, the passphrase sits behind a *Have a passphrase?* link.

**Email codes.** The person enters an address and receives a code that lasts 10 minutes and works once, for that sign-in only. Five wrong codes discard the sign-in, as five wrong passphrases do. To limit what the page can be made to send, a sign-in can ask for three codes, an address is sent five an hour, one client address can ask for five at once and ten an hour, and the server sends `MAIL_MAX_PER_HOUR` an hour in all. When that last limit is reached nobody can be sent a code, and the log gets a warning.

**Accounts.** The address is passed to Resend to deliver the code and is not stored. The account is an HMAC-SHA256 of the lower-cased address, without any `+tag`, under `MCP_ACCOUNT_SECRET`, so the stored ID can't be turned back into the address, or tested against a guess, without the secret. An account holds its ID, when it was created, when it was last used (a sign-in or a token refresh) and whether it is blocked. Accounts not used for 180 days are deleted, with their tokens. `traintracker forget EMAIL` deletes one on request; `traintracker block EMAIL` shuts one out, and `UPDATE mcp_auth.accounts SET blocked = false` lets them all back in.

Everyone who signs in with the passphrase shares one account, `passphrase`. Use a long random passphrase: `serve-http` warns at start-up about one shorter than 20 characters. One client address can try ten passphrases at once and ten an hour, and after 30 wrong passphrases in an hour, over all sign-ins, the passphrase is refused for the rest of that hour (email sign-in is not affected).

**Rate limit.** Tool calls over HTTP are limited per account: `RATE_LIMIT_BURST` calls at once, refilled at `RATE_LIMIT_PER_MINUTE`. One Darwin key serves every user, and the limit stops one account spending the whole allowance. A call pays for one request to Darwin; a call that makes more (a long board read page by page, a journey with several legs) is charged one for each further request, so the account's next calls wait longer. A journey plan counts as three calls. A call over the limit comes back as a tool error, *Too many requests. Try again in N seconds.*, which the model can read and relay. The counts are held in memory, so a restart clears them. The static token counts as one account. The stdio server is not limited.

**Where a client can return to.** A client registers the callback the browser goes back to after sign-in. Only callbacks on `MCP_REDIRECT_HOSTS` (Claude and ChatGPT by default), over https, and loopback addresses (desktop clients such as Claude Code) are accepted. OAuth answers some errors by redirecting to the callback before anyone has signed in, so with any callback allowed a link to the server could be made to send people to any site. To let another app connect, add its callback host.

**Before sign-in.** Registration and the start of a sign-in need no account, so they are limited another way. A client address can register 30 clients at once and 20 a minute, and start 20 sign-ins at once and 10 a minute; over that the answer is 429 with `Retry-After`. The address comes from `CLIENT_IP_HEADER` where that is set, and from the connection otherwise. These endpoints take bodies up to 16 KiB, a registration up to 4 KiB and a sign-in request up to 8 KiB. The server holds 10,000 registered clients at most, and a registration deletes clients that are a week old with no tokens and no sign-in under way (an assistant registers afresh each time it connects).

**Running it locally.** `MAIL_BACKEND=console` with `MCP_ACCOUNT_SECRET` set writes the code to the log in place of sending it. Don't use it on a host whose logs other people can read.

## Site

`serve-http` also serves a small public site from the same app, so there is one deploy and one domain:

| Path | Page |
|---|---|
| `/` | What the server does, the connector address with a copy button, how to add it to Claude and ChatGPT, three example prompts |
| `/docs` | Each tool in plain English, three worked examples with what the answer contains, the limits, data sources, support |
| `/privacy` | Privacy policy (UK GDPR): what is processed, why, for how long, and by whom |
| `/terms` | Terms of use: the service as is, accuracy, liability, fair use, data licences, governing law |

The pages are files in [`src/traintracker/site/`](src/traintracker/site): HTML fragments placed inside `layout.html`, one stylesheet and one script for the copy button. The icon is `icon.svg`, with PNGs rendered from it at 32, 180 and 512 pixels; `icon-512.png` is the one uploaded to the directory listings, and `/favicon.ico` answers with the 32-pixel PNG. To change the icon, edit the SVG and render the PNGs again, for example `magick -background none -density 1200 icon.svg -resize 512x512 PNG32:icon-512.png`. The link-preview card is `share.png`, 1200 by 630, rendered from `share.svg`: `magick -font /System/Library/Fonts/Menlo.ttc share.svg PNG24:share.png` (ImageMagick's own SVG renderer needs the font's file). There is no build step. They are filled in once at start-up with the server's own values (the connector address from `MCP_PUBLIC_URL`, the rate limit), so a copy deployed elsewhere describes itself. The privacy policy and terms name this repository's deployment and its operator: change `privacy.html`, `terms.html` and the support address when you deploy your own.

The stylesheet and script are linked by an address that carries a hash of the file, so a browser fetches a changed file at once and can cache an unchanged one for an hour. The pages set no cookies and load nothing from another origin; the `Content-Security-Policy` header allows only the site's own stylesheet and script. They follow the reader's light or dark setting and work at phone width.

## MCP Registry

[`server.json`](server.json) describes the server for the official [MCP Registry](https://registry.modelcontextprotocol.io): the name `live.traintrackr/traintracker`, the version, and one remote, streamable HTTP at `https://traintrackr.live/mcp`. It follows the registry's `2025-12-11` schema. A test keeps its version in step with `pyproject.toml`.

Version 0.1.0 is published. CI doesn't publish: a new version is published by hand, with `mcp-publisher login dns` and then `mcp-publisher publish`. A `live.traintrackr/` name needs proof that you hold the domain, which is a TXT record on the apex of `traintrackr.live` carrying the public half of the signing key. For your own deployment, change the name and the URL to your domain.

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

CI runs the same environments with the versions of uv, tox and tox-uv written into [`.github/workflows/tox.yml`](.github/workflows/tox.yml), and its Actions pinned to a commit. Dependabot ([`.github/dependabot.yml`](.github/dependabot.yml)) proposes updates to `uv.lock` and to the Actions each week; the uv and tox versions are raised by hand, uv in `render.yaml` as well.

| Environment | Runs |
|---|---|
| `format` | `ruff format --check` |
| `lint` | `ruff check` |
| `type` | `mypy` (strict) |
| `tests` | `pytest`: importer, STP rules, planner, download, clients, demo mode, tools end to end, sign-in, rate limit, site, Darwin usage |

Tests use a synthetic SCHEDULE feed in Network Rail's JSON format (`tests/feedgen.py`) and API fixtures shaped on the published Darwin schema. They aren't live recordings, so the first run against real services is the final check.

### Preview servers

[`.claude/launch.json`](.claude/launch.json) has two servers that Claude Code's preview pane can start. Both run in demo mode against the development Postgres above, ignore `.env`, and listen on `127.0.0.1` only. Set `PREVIEW_DATABASE_URL` to use another database.

| Name | Runs | Use it for |
|---|---|---|
| `web` | `traintracker serve-http` on port 8000, or a free port if that is taken | The public site, the sign-in page and `/mcp`. Sign-in codes are written to the server's log (`MAIL_BACKEND=console`) |
| `mcp-inspector` | The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) (version 2, through `npx`) on port 6274, with this server as its stdio target | Calling the tools by hand. Its Apps tab draws a tool's MCP App, where it has one |

The Inspector needs Node 22.19 or later. It starts a local server that can run commands, guarded by a token it makes at each launch and puts into the page it serves; the config leaves that on.

```
src/traintracker/
  server.py      MCP tools, source fallback, live overlay, CLI
  http_app.py    Streamable-HTTP entry point: auth wiring, Host check, health check
  oauth.py       OAuth provider (Postgres), sign-in page, accounts
  mail.py        Sends the sign-in code (Resend, or the log in development)
  ratelimit.py   Per-account limit on tool calls
  site/          Public pages: landing, docs, privacy policy, terms
  timetable.py   SCHEDULE importer and Postgres queries (STP resolution)
  planner.py     Connection Scan journey planner, London links
  darwin.py      Rail Data Marketplace LDBWS client
  usage.py       Count of requests sent to Darwin, against the allowance
  stations.py    Station search and name resolution
  models.py      Output models shared by all sources
  demo.py        Demo mode: generated timetable and in-process Darwin
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
