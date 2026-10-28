# Todo API

[![CI](https://github.com/JrSaban/python-agentic-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/JrSaban/python-agentic-lab/actions/workflows/ci.yml)

A REST API for managing todos and categories, built with **FastAPI** and **Clean Architecture** in Python, and the lab for two learning goals. The first is moving from PHP/Laravel to Python, FastAPI and SQLAlchemy: I write the production code, from JWT authentication with rotating refresh tokens to a generic repository layer on Python 3.12's native generics. The second is learning to build software *with* AI agents, and *for* them. Claude Code explains, reviews my code and writes the tests and the documentation, while the API is exposed to agents through an MCP server, with personal API tokens, human approval of bulk actions, an audit log, and evals that measure how a real Claude model uses it.

## Tech stack

- **Python 3.12** · [uv](https://docs.astral.sh/uv/) for dependency management
- **FastAPI** — async web framework, automatic OpenAPI docs
- **SQLAlchemy 2.0** (async) + **Alembic** — ORM and migrations
- **PostgreSQL** · **Redis** (cache, refresh tokens, rate-limit counters, idempotency keys, job queue)
- **Taskiq** — async background jobs and scheduling (worker + scheduler processes)
- **MCP Python SDK** — the MCP server that lets an AI agent use the API
- **Anthropic Python SDK** — evals that run a real Claude model on the MCP tools
- **Pydantic v2** — request/response validation and serialization
- **argon2-cffi** — password hashing · **PyJWT** — access tokens
- **structlog** — JSON logs with a request ID on every line
- **pytest** + **httpx** — unit and integration tests, on in-memory SQLite and `fakeredis`
- **Ruff** — linting and formatting
- **Docker Compose** · **GitHub Actions**

## Features

- **MCP server for AI agents** — an agent such as Claude Code can list, read, create, update, complete, delete and restore todos, and list and read categories, through 14 tools. It is a plain client of the API with a personal API token, so it goes through the same permissions, rate limits and idempotency as any client. It runs locally for one user, or as a remote HTTP server shared by many agents, each with its own token
- **Remote MCP server, designed from a security review** — tokens are issued for one usage: an `mcp` token only works through the remote server, which proves itself to the API with a service key, so a stolen one is useless against the API directly. Each request carries its own user's token, never shared inside the server; requests without a token are refused before the MCP protocol, the server keeps no session, rejects unknown hosts (DNS rebinding) and passes each agent's IP to the API's rate limit
- **Human approval for an agent's bulk actions** — an agent deletes one todo at a time directly, but deleting all completed todos at once only creates a request, which the user approves from a login session; the agent can follow it, or cancel it when the user asks, but never approve it. One request per action can be pending at a time, it expires after 7 days, and requests are kept as history for 3 months after that. The same action from a login session runs immediately
- **Evals for the MCP tools** — a command runs a real Claude model as an agent through ten scenarios, five times each, and prints a pass rate per scenario: does it pick the right tool and arguments, does it say a bulk deletion awaits approval instead of working around it, does it refuse instructions hidden in a todo title or a category name
- **Authentication** — registration, login, short-lived JWT access tokens, and opaque refresh tokens that are rotated on every use and revoked on logout
- **Personal API tokens** — long-lived, revocable tokens for agents (up to 5 active per user, 30 days, 90 days or no expiry), shown once at creation and stored hashed; they can't manage the account or act as an admin
- **Role-based access control** — regular users vs. admins, with dedicated endpoints to promote/demote or (de)activate accounts (protected against locking out the last remaining admin)
- **Audit log** — every change made through the API to todos, categories, accounts, API tokens and approval requests is recorded (clean-ups excepted, scheduled or started by an admin): who acted, whether as a human or through which API token, on what, which fields changed (names only, never values), and when. Each user reads the entries about their own resources, admins all of them; an agent can't read the log. Entries are kept a year
- **Ownership & permissions** — a todo is private to its owner (admins see everything); a category is visible to everyone but only its creator or an admin can edit it, and only an admin can delete it
- **Todos ↔ Categories** — many-to-many relationship, with optional eager-loading (`?include=categories`) and filtering by category
- **Pagination & filtering** — every paginated list endpoint supports `skip`/`limit` (page sizes of 10, 25, 50 or 100); most also take resource-specific filters (status, category, role, ...), a free-text search, or both
- **Soft deletes** on todos and categories — a deleted row stays in the database but is invisible to the API, like Laravel's `SoftDeletes`, and is deleted for good by a weekly background job after 30 days, like Laravel's `Prunable`. Until then, deleted todos can be listed and restored
- **Rate limiting** — a general per-user / per-IP request quota, plus stricter limits on failed logins and on wrong-password attempts when changing your email or password or creating an API token. Behind a reverse proxy, the client IP is read from `X-Forwarded-For`, but only for the proxies listed in `TRUSTED_PROXIES`, so a client can't pick its own IP
- **Idempotency keys** — an `Idempotency-Key` header on `POST /todos`, `POST /categories` and `POST /todos/delete-completed`, so a retried request returns the original response instead of creating a duplicate; optional for a login session, required for an API token
- **Background jobs with tracking** — every run is recorded (pending, running, succeeded, failed) and readable through the API, each user seeing their own jobs and admins all of them, scheduled runs included; admins can also start the pruning on demand
- **Redis caching** on category reads, invalidated on every write
- **Structured logging** — one JSON line per request, correlated by an `X-Request-ID` header
- **Alembic migrations**, including a 3-step pattern (add nullable → backfill → enforce `NOT NULL`) for introducing foreign keys on already-populated tables

## Design highlights

A few decisions worth a second look if you're skimming the code:

- **Ownership lives in the query, not as an afterthought.** `TodoRepository` takes an `owner_id: int | None` filter; the service computes it as `current_user.id`, or, for an admin, the optional `owner_id` query parameter (no filter when absent). Requesting someone else's todo returns `404`, never `403` — the row simply doesn't match the filter, so the API never confirms whether an ID belongs to another user.
- **Uniqueness is enforced by the database, not by a check-then-insert.** Category names, emails and pseudos are unique case-insensitively through functional unique indexes (`lower(name)`). Services don't query first: they insert, catch the `IntegrityError`, and return a clean `409` — so two concurrent requests can't both slip through. A dedicated test runs the two inserts concurrently to prove it. The category index is partial (`WHERE deleted_at IS NULL`), so the name of a soft-deleted category can be reused.
- **Refresh tokens are stored hashed and rotated atomically.** Redis only holds the SHA-256 of a token, never the token itself. Each `/refresh` consumes it with a single `GETDEL`, so the same token can't be exchanged twice, even by two requests arriving at the same time. Rotation alone would let an active session renew itself forever, so a separate, longer-lived marker caps the session's absolute lifetime regardless of activity. Changing your password or your email revokes it outright, the same way logging out does.
- **Login takes the same time whether the email exists or not.** An unknown email still runs a full Argon2 verification, against a dummy hash computed once at startup, instead of short-circuiting — otherwise the response time alone would reveal which emails are registered, even behind an identical error message.
- **Rate limits that only punish failures.** Login attempts are counted per IP *and* per email, so neither rotating emails from one machine nor spreading guesses across machines gets around the limit — and only failed attempts count, so a legitimate user is never throttled for logging in. Each attempt is reserved with an atomic `INCR` before the password is checked, then given back unless it failed, so attempts sent in parallel can't all slip under the limit. The services know nothing about it: limits are checked and counted at the router level.
- **Retries that can't create duplicates, even concurrent ones.** A client that times out and retries is the classic duplicate-maker — and the retry usually arrives while the first request is still running. The idempotency key is therefore claimed atomically in Redis (`SET NX`) *before* the request runs, so a concurrent retry gets a `409` instead of running twice, as long as the first request finishes within the lock's TTL (30 seconds). Only successful responses are stored, and only once the database transaction has actually committed.
- **An agent gets no back door.** The MCP server lives outside `src/` and never imports it: it only calls the HTTP API, with the user's API token. Everything built to protect the API — rate limits, idempotency, ownership — therefore applies to the agent too, and an API token can't act as an admin or manage the account. Retrying a creation reuses the agent's idempotency key, so a blind retry never duplicates.
- **A human stays in the loop where it matters.** An agent's bulk deletion becomes a request that freezes the exact todos it targets, chosen by the server rather than by the agent; the user approves that list, nothing more. Approving is a single conditional `UPDATE` on the pending row, so two approvals at once can't run the deletion twice, and an expired request can't be approved at all. Stored texts — especially category names, shared between users — can carry instructions aimed at the agent: the MCP server tells the model they are data, but the real limit is structural, since a token can neither manage the account, nor act as an admin, nor approve anything — nor create or rename a category, the one text every other user's agent would read.
- **An agent's behaviour is measured, not assumed.** The evals check what the agent actually did — the rows left in the database and the tools it called — rather than what it says it did. A cheaper model acts as a judge only for what can't be checked that way, the wording of a reply. Each run starts from a fresh in-memory app whose requests commit as in production, and the harness itself is tested in CI against a scripted fake model, at no cost.
- **A cache that never takes the API down.** Paginated category lists are invalidated with a generation counter (one `INCR` instead of hunting for every cached page). Every Redis call on the cache path is allowed to fail: the request falls back to PostgreSQL and logs a warning, except a failed read of the generation counter, which silently falls back to generation 0.
- **Generic repository layer.** `BaseRepository[ModelT: Base]` (`src/core/repository.py`) uses Python 3.12's native generic syntax (`class Foo[T]`) to share pagination, counting and typed filtering across every repository, while each resource keeps its own fully-typed filter parameters — no dynamic/untyped filter dicts. A `SoftDeleteRepository` layer adds restoring and the pruning of old soft-deleted rows to the two models that need it, and only to them.
- **Clean Architecture, enforced consistently.** Every module follows the same `router → service → repository → models` layering; services raise framework-agnostic exceptions (`NotFoundError`, `ForbiddenError`, ...) mapped to HTTP status codes in one place, never `HTTPException` scattered through the business logic.
- **Tested at two levels.** Fast unit tests exercise each service in isolation with mocked repositories; integration tests run the full FastAPI stack against a real (in-memory) database. CI also applies every migration to a real PostgreSQL instance, and audits the locked dependencies against known vulnerabilities (`pip-audit`) — the Docker image isn't published while one is reported.

## Getting started

```bash
git clone https://github.com/JrSaban/python-agentic-lab.git
cd python-agentic-lab
cp .env.example .env
```

Set a real `JWT_SECRET_KEY` in `.env` — the app refuses to start with the placeholder, or with anything shorter than 32 bytes:

```bash
openssl rand -hex 32
```

Then start everything:

```bash
docker compose up -d --build                          # API on :8000, Postgres on :5432, Redis on :6379, job worker + scheduler
docker compose exec api uv run alembic upgrade head   # create the tables
```

Interactive API docs (Swagger UI) are available at [http://localhost:8000/docs](http://localhost:8000/docs) when `DEBUG=True`, as in `.env.example`. They are off by default.

<details>
<summary>Running the API on the host</summary>

```bash
docker compose up -d db redis
uv sync
uv run alembic upgrade head
uv run uvicorn src.main:app --reload --no-proxy-headers   # client IPs come from TRUSTED_PROXIES only
uv run taskiq worker src.core.broker:broker --fs-discover        # background jobs, in another terminal
uv run taskiq scheduler src.core.broker:scheduler --fs-discover  # scheduled jobs (one instance only)
```

</details>

### Connecting an AI agent (MCP)

The repository ships a `.mcp.json`, so Claude Code offers the `todo-api` server when opened in the project. It needs a personal API token of a **non-admin** account:

1. Log in (`POST /api/v1/login`), then create a token with `POST /api/v1/api-tokens` and your current password. The token (`tda_...`) is shown only once.
2. Copy `.env.mcp.example` to `.env.mcp` (gitignored) and put the token in `TODO_API_TOKEN`.
3. With the API running, open Claude Code in the project and approve the server; `/mcp` lists its tools.

**Remote mode.** The same server can run over HTTP, shared by several agents, each sending its own token:

1. Generate a service key (`openssl rand -hex 32`) and put it in `.env` as `MCP_SERVICE_KEY`. The API and the MCP server both read it there.
2. Start the stack with the MCP server: `docker compose --profile mcp up -d`. It listens on `http://localhost:8001/mcp`.
3. Each user creates a token with `"usage": "mcp"` (`POST /api/v1/api-tokens`), and the agent sends it as `Authorization: Bearer tda_...`. An `mcp` token works only through this server, a default `api` token only without it.

Before exposing it beyond your machine, put it behind an HTTPS reverse proxy and set `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` and `MCP_TRUSTED_PROXIES` for it. The design and its threats are in [`docs/remote-mcp-security-review.md`](docs/remote-mcp-security-review.md).

## Running the tests

No Postgres or Redis needed — the suite runs on in-memory SQLite and `fakeredis`.

```bash
uv run pytest              # full suite
uv run ruff check .        # lint
uv run ruff format .       # format
```

## Running the evals

The evals call the Anthropic API, so every run is billed and they never run in CI. They need no database or Redis either.

1. Create an API key in the [Claude Console](https://console.anthropic.com/).
2. Copy `.env.evals.example` to `.env.evals` (gitignored) and put the key in `ANTHROPIC_API_KEY`.
3. Run:

```bash
uv run --env-file .env.evals python -m evals
```

## API overview

All routes are prefixed with `/api/v1`, except `GET /health`, which needs no authentication and is never rate-limited. `POST /todos`, `POST /categories` and `POST /todos/delete-completed` accept an `Idempotency-Key` header, required when authenticating with an API token.

| Method | Path | Description |
|---|---|---|
| `POST` | `/login` | Authenticate, returns an access token and a refresh token |
| `POST` | `/refresh` | Exchange a refresh token for a new pair |
| `POST` | `/logout` | Revoke the current refresh token *(login session only)* |
| `POST` | `/users` | Register a new account |
| `GET` | `/users/me` | Current user's profile |
| `GET` | `/users` | List users *(admin only)* |
| `GET` `PATCH` | `/users/{id}` | View / update a profile (yourself, or anyone as an admin) *(updating: login session only)* |
| `PATCH` | `/users/me/password` | Change your own password *(login session only; logs you out; can also revoke your API tokens)* |
| `PATCH` | `/users/me/email` | Change your own email, current password required *(login session only; logs you out)* |
| `PATCH` | `/users/{id}/email` | Change another user's email *(admin only, logs them out)* |
| `PATCH` | `/users/{id}/active` `/admin` | (De)activate or promote a user *(admin only)* |
| `GET` `POST` | `/todos` | List (own todos, or all for admins) / create a todo |
| `GET` `PATCH` `DELETE` | `/todos/{id}` | View / update / delete a todo you own |
| `GET` | `/todos/deleted` | List your deleted todos (all of them for admins) |
| `POST` | `/todos/{id}/restore` | Restore a deleted todo |
| `POST` | `/todos/delete-completed` | Delete your completed todos *(with an API token: creates an approval request instead)* |
| `GET` | `/approval-requests` | List approval requests (your own, or all for admins) |
| `GET` | `/approval-requests/{id}` | An approval request's status and result |
| `POST` | `/approval-requests/{id}/approve` | Approve one of your requests *(login session only)* |
| `POST` | `/approval-requests/{id}/reject` | Reject or cancel one of your requests |
| `GET` `POST` | `/categories` | List / create a category *(creating: login session only)* |
| `GET` `PATCH` `DELETE` | `/categories/{id}` | View / update (creator or admin, login session only) / delete (admin only) |
| `GET` | `/categories/{id}/todos` | Todos belonging to a category |
| `POST` | `/maintenance/prune` | Start the soft-delete pruning now, returns the job to follow *(admin only)* |
| `GET` | `/jobs` | List jobs (your own, or all for admins) |
| `GET` | `/jobs/{task_id}` | A job's status, result or error |
| `POST` | `/api-tokens` | Create a personal API token, current password required *(login session only; the token is shown once)* |
| `GET` | `/api-tokens` | List your API tokens (never their value) |
| `DELETE` | `/api-tokens/{id}` | Revoke one of your API tokens *(login session only)* |
| `GET` | `/audit-logs` | Audit entries about your resources (all of them for admins), filterable by action, resource and API token *(login session only)* |
| `GET` | `/audit-logs/{id}` | One audit entry *(login session only)* |

## Project structure

```
src/
├── core/            # cross-cutting: database, redis, config, logging, exceptions, security,
│                    # rate limiting, idempotency, HTTP middlewares, generic repository,
│                    # task broker
├── modules/
│   ├── auth/        # login, refresh, logout, JWT and API-token validation
│   ├── api_tokens/   # personal API tokens for agents
│   ├── approvals/    # approval requests: an agent's bulk actions wait for a human
│   ├── audit/        # audit log: who changed what, human or agent
│   ├── users/        # user profiles, admin management
│   ├── todos/        # todos, ownership
│   ├── categories/   # categories, creator/admin permissions, cache
│   ├── jobs/         # job tracking: table, Taskiq middleware, status endpoints
│   ├── maintenance/  # background jobs spanning several modules (weekly pruning)
│   └── todos_categories/  # many-to-many association table
└── main.py          # app wiring: routers, middleware order, exception handler
mcp_server/          # MCP server for AI agents, stdio or HTTP: an HTTP client of the API, never imports src/
docs/                # design documents (the remote MCP server's security review)
evals/               # evals: a real Claude model uses the MCP tools, scenarios check what it did
alembic/versions/    # migrations
tests/               # integration tests + tests/unit/ for isolated service tests
```

Branch, commit and PR conventions are in [CONTRIBUTING.md](CONTRIBUTING.md).
