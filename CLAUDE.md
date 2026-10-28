# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It records this project's own decisions only. How FastAPI, SQLAlchemy, Pydantic, pytest or Python work in general stays out, even when it caused a bug here: before adding a sentence, check it wouldn't be just as true in another FastAPI project.

## Project overview

FastAPI To-Do List API built with a Clean Architecture layering, used as a learning project: the author is an experienced PHP/Laravel developer learning Python/FastAPI/SQLAlchemy by building it. Python 3.12, managed with `uv`. PostgreSQL is the database; Redis holds the category cache, the refresh tokens, the rate-limit counters, the idempotency keys, and the Taskiq job queue and results. A separate MCP server (`mcp_server/`) lets an agent use the API, and `evals/` measures how a real Claude model uses it.

Language conventions, all deliberate:

- Module-level docstrings (top of file) and API error messages (`detail`) are in **French**. Several module docstrings carry a Laravel analogy ("Équivalent conceptuel de ... dans Laravel") — keep them, and add one when a new cross-cutting piece has an obvious Laravel counterpart.
- Function, method and class docstrings are in **English**. Many existing ones are still in French from before this rule.
- Test docstrings, `CLAUDE.md`, `README.md` and `CONTRIBUTING.md` are in English.

Branch names, commit messages and PR rules live in `CONTRIBUTING.md`; the PR template is `.github/pull_request_template.md` (Why / Changes / Notes). Planned work is in `ROADMAP.md`: an item is removed from it in the same PR that finishes it.

## Commands

Everything runs through `uv run`.

```bash
docker compose up -d --build            # API on :8000 (hot-reload on ./src) + Postgres :5432 + Redis :6379 + worker + scheduler
uv run uvicorn src.main:app --reload --no-proxy-headers   # API on the host, against the compose Postgres/Redis

uv run taskiq worker src.core.broker:broker --fs-discover --reload   # job worker (queue:work)
uv run taskiq scheduler src.core.broker:scheduler --fs-discover      # scheduler, ONE instance only

uv run --env-file .env.mcp python -m mcp_server   # MCP server over stdio — normally launched by Claude Code from .mcp.json
docker compose --profile mcp up -d                # + the remote MCP server over HTTP on :8001 (needs MCP_SERVICE_KEY in .env)
uv run --env-file .env.evals python -m evals      # MCP evals: calls the paid Anthropic API, never in CI

uv run pytest                                  # full suite
uv run pytest tests/unit/                      # unit tests only
uv run pytest tests/test_todos.py::test_name   # one test

uv run ruff check --fix . && uv run ruff format .

uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

### Environment

`.env` targets a host-run API (`POSTGRES_HOST=localhost`, `REDIS_URL=redis://localhost:6379/0`). `docker-compose.yml` overrides `DATABASE_URL` and `REDIS_URL` so they point at the `db`/`redis` service names — a new connection setting needs both places. `api`, `worker` and `scheduler` share one `x-app` YAML anchor and differ only by `container_name`, `command` and `ports`.

The MCP server's dependencies live in a separate `mcp` dependency group, installed by default (`[tool.uv] default-groups`) so tests and CI get it, and excluded from the API's Docker image (`--no-group mcp`). That image installs the API's own dependencies only (`--no-dev --no-group mcp --no-group evals`), then sets `UV_NO_SYNC=1`: without it, every `uv run` in a container (the API, the worker, the scheduler, `alembic`) would install the default groups back at startup. The group lists every package `mcp_server/` imports except `pydantic` and `starlette`, which only come through `mcp`; `pydantic-settings` and `uvicorn` are listed even though the API depends on them too: `Dockerfile.mcp` installs that group alone (`--only-group mcp`) and copies `mcp_server/` only, so a missing entry breaks the MCP image, never the tests. Its `CMD` calls `.venv/bin/python` directly: `uv run` would sync the whole project. The compose `mcp` service sits behind the `mcp` profile (`docker compose --profile mcp up`), because without `MCP_SERVICE_KEY` it refuses to start; it reads the same key as the API from `.env`, through `${MCP_SERVICE_KEY:-}`. It has a fixed address (`172.30.0.10`), which the `x-app` anchor sets as the API's `TRUSTED_PROXIES`: changing one means changing the other. The default network's subnet is pinned for that, and its `ip_range` (`172.30.0.128/25`) keeps every dynamic address out of the lower half: without it, another container could get `.10` first, and the API would trust its `X-Forwarded-For`. An existing network keeps its old settings until it is recreated with `docker compose --profile mcp down` — without the profile, `down` leaves `todo_mcp` running, attached to a network that no longer exists. The local server's token sits in `.env.mcp` (gitignored, copied from `.env.mcp.example`), loaded by `uv run --env-file`. The evals follow the same pattern: `anthropic` in an `evals` group (default, `--no-group evals` in the image), the key in `.env.evals` (from `.env.evals.example`). Neither `.env.*` file is covered by a wildcard in `.gitignore`: a new one must be added there by name.

`Settings` refuses to start if `JWT_SECRET_KEY` is shorter than 32 bytes or starts with `change-me`, which is exactly what `.env.example` ships. A fresh `cp .env.example .env` therefore crashes at import time until a real secret is set (`openssl rand -hex 32`). Anything that imports `src.core.config` needs the variable, including Alembic — which is why the CI `migrations` job sets it.

### CI

`.github/workflows/ci.yml` runs five independent jobs on every push and PR: `lint`, `format` (`ruff format --check`), `test`, `migrations` (`alembic upgrade head` against a real Postgres service) and `audit`. The `migrations` job is the only place the migrations themselves, and the Postgres dialect in general, are exercised — the test suite runs on SQLite with `Base.metadata.create_all`, never through Alembic. `audit` runs `pip-audit` (pinned, through `uvx`, not a project dependency) on the committed `uv.lock`, every dependency group included. `uv` is pinned to the same version in the five jobs (`setup-uv`'s `version:`) and in `Dockerfile` and `Dockerfile.mcp` (`ghcr.io/astral-sh/uv:<version>`): change all seven together. Only the API image is built in CI and published. The workflow's default `permissions` are read-only; only `docker` widens them, to push the image. A sixth job, `docker`, pushes `ghcr.io/<repo>:latest` on pushes to `main` once the other five pass, `audit` included: an image with a known vulnerable dependency is not published.

### Migrations

`alembic/env.py` imports every model class explicitly so autogenerate can see all tables — a new module's models must be added there too. The `todos_categories` table needs no import: it comes in through `Todo`/`Category`. Alembic's default template is not Ruff-compliant (imports, `Union[...]` hints): fix the generated file to match the existing ones in `alembic/versions/`. Review what autogenerate emits for functional indexes (see "Uniqueness" below): the existing migrations declare them by hand with `sa.literal_column("lower(name)")`.

## Tests

Integration tests (`tests/test_*.py`) go through the full FastAPI stack with the `client` fixture, which overrides two dependencies: `get_db_session` (in-memory SQLite via `aiosqlite`, tables created and dropped around every test) and `get_redis_client` (a per-test `fakeredis.FakeAsyncRedis`, exposed as the `redis_client` fixture so a test can inspect or seed keys directly). The rate-limit and idempotency middlewares call `get_redis_client()` themselves, outside dependency injection, so `dependency_overrides` doesn't reach them: the `client` fixture also monkeypatches `src.core.redis.redis_client` with the same fake. Every test starts with an empty fake, hence fresh rate-limit counters and idempotency keys. Both middlewares resolve the user themselves (JWT signature or API-token lookup), not through `get_current_user`: a test that needs them to see a user sends a real `Authorization: Bearer` header (`create_access_token`, or an API token), even with `authenticated_client`. Unit tests (`tests/unit/`) isolate one `*Service` by passing `AsyncMock` for its repositories and its Redis client; the module-level functions of `src/core/security.py` are called directly by services rather than injected, so they are patched at the import site: `@patch("src.modules.<module>.service.<function_name>")`.

Which kind to write:

- Real SQL logic (filters, joins) → integration test. Mocking the repository proves nothing about the query.
- Service branching/orchestration, and failure modes that `fakeredis` can't produce (`RedisError`, corrupted cache payload) → unit test with `side_effect`.
- Cache behaviour (stored, served, invalidated) → integration test with `redis_client`. The "served from cache" tests prove a hit by inserting a row through the repository directly, which bypasses the service's invalidation, then asserting the API still returns the old result.

`conftest.py` force-sets `JWT_SECRET_KEY` with `os.environ[...]` but `DEBUG` with `os.environ.setdefault` right above it, both before any `src.*` import. The difference is deliberate, not an inconsistency to fix: tests must never depend on the secret in a developer's `.env`, while `DEBUG` may be turned on from the shell to see logs. It also force-sets `APP_ENV=test`, which makes `src/core/broker.py` build an in-memory broker (see "Background jobs"), and swaps `security._password_hasher` for an Argon2 hasher at minimal cost — the defaults halved the suite's speed. `_DUMMY_PASSWORD_HASH` is computed at import with the real parameters, so an unknown-email login keeps its cost.

The SQLite test engine runs `PRAGMA foreign_keys=ON` on every connection, so tests enforce FKs and `ON DELETE CASCADE` like Postgres. A test row must point at rows that really exist — a hardcoded `created_by_id=1` with no such user now fails.

All test sessions share **one** SQLite connection (`StaticPool`), and the `get_db_session` override never commits. A request carrying an API token makes the middlewares open their own session, and closing it rolls back that shared connection: a test must **commit** its setup data before such a request, and writes made by earlier requests in the same test are lost (assert through responses, not row counts).

### Auth fixtures

`current_user`/`authenticated_client` (plain user) and `current_admin_user`/`authenticated_admin` (admin) create the `User` row directly through `UserRepository` and override `get_current_user` (`_as_session_user`), so no JWT is involved. The override also sets the request's audit actor, as the real dependency does, and must stay `async`. This is the default for permission-matrix tests. Only `test_get_me_with_real_token`/`test_get_me_without_token_returns_401` in `tests/test_users.py` send a real `Authorization: Bearer` header obtained from a real login, to prove the JWT/dependency wiring end to end. `tests/test_auth.py` uses real registration + `/login` for the refresh-token flows, because those need tokens actually issued into Redis.

`authenticated_client` and `authenticated_admin` return the *same* `client` object: the override is a single entry in `app.dependency_overrides`, not a property of the client. Requesting both in one test does not give two identities — whichever fixture is set up last (parameter order in the test signature) wins for the whole test. A test that needs a second user takes the `other_user` fixture, a plain `User` row with no client attached, and creates that user's data through the repository (`TodoRepository(db_session).create(owner_id=other_user.id, ...)`), never through the API.

`test_create_category_concurrent_same_name_no_duplicate` is the model for race-condition tests: it bypasses the HTTP layer, opens two independent sessions from `test_session_factory`, calls the service in both under `asyncio.gather`, and checks the row count from a third session. A race that lives in the HTTP layer instead (a rate-limit dependency) goes through the client under `asyncio.gather`, with the service replaced through `app.dependency_overrides` by a fake that `await`s before failing, like `SlowFailingService` in `tests/test_attempt_limits.py`: the shared test session can't serve concurrent requests, and without the pause each request would finish before the next one starts.

## Architecture

### Module layering

Each domain lives under `src/modules/<name>/`, in strict call order:

```
router.py → service.py → repository.py → models.py
```

- **`repository.py`** — the only layer that builds or executes SQL. Takes and returns ORM instances or primitives, never raises HTTP-flavoured errors, imports schemas only to type its inputs (`*Create`/`*Update`). Lets `IntegrityError` propagate.
- **`service.py`** — business and orchestration logic: permissions, resolving foreign IDs into objects, deciding what to eager-load, translating `IntegrityError`, caching. May depend on another module's repository (`TodoService` takes a `CategoryRepository` to resolve `category_ids`; `AuthService` takes a `UserRepository`). Raises the exceptions from `src/core/exceptions.py`, never `fastapi.HTTPException`.
- **`router.py`** — endpoints, the `get_<name>_service` factory and the `*ServiceDep` alias. Request validation and response serialization happen here. Convention: in a handler signature the injected service comes **before** `current_user`, path params and the request body.

Services return ORM models and the router serializes them through `response_model`. The one deliberate exception is the cached read path of `CategoryService` (`list_categories`, `get_category_cached`), which returns `CategoryResponse` objects: a cache hit yields JSON, and an ORM instance can't be rebuilt from it, so both the hit and the miss path return the schema to keep one return type. Don't generalize this to uncached services.

`auth` and `maintenance` have no `models.py`/`repository.py` (they own no table; API tokens have their own `api_tokens` module), and `todos_categories` holds only the association `Table`. A module may also have a `tasks.py` for its background jobs (see "Background jobs").

`src/core/` is what every module shares:

| File | Role |
|---|---|
| `database.py` | Async engine, `Base` (declares `id` — no model repeats it), `get_db_session`: **one transaction per request**, committed after the handler returns but **before the response is sent**, rolled back on any exception. Repositories only `flush()`. Always inject it through `DbSessionDep` (`scope="function"`), never `Depends(get_db_session)` directly: that is what makes the commit happen before the response is sent, which idempotency and the job launch rely on. |
| `config.py` | `pydantic-settings`, reads `.env`. |
| `exceptions.py` | `AppBaseError` and its subclasses, generic rather than per-resource, each carrying its `status_code`. A single `@app.exception_handler(AppBaseError)` in `main.py` handles them all, so a new error type is just a subclass with a `status_code`. `InvalidPasswordError(ForbiddenError)` is raised for a wrong current password **only**: it is the only error the sensitive-action dependency keeps counted, so a 403 for any other reason never does. |
| `repository.py` | `BaseRepository` and `SoftDeleteRepository` (below). |
| `broker.py` | The Taskiq `broker` (in-memory under tests, with `JobTrackingMiddleware`) and `scheduler` (see "Background jobs"). |
| `schemas.py` | `PaginatedResponse[T]` and `LimitQuery`. |
| `security.py` | Argon2 password hashing, JWT access tokens, refresh- and API-token generation, `hash_token` (SHA-256, shared by both). `API_TOKEN_PREFIX` is a constant, not a setting: `get_current_user` recognises API tokens by it, so changing it would orphan every token already issued. `expected_api_token_usage` (see "API tokens") imports `ApiTokenUsage` from `api_tokens/models.py`, so that module must never import `security.py`. |
| `redis.py` | The shared async client (`decode_responses=True`, so values come back as `str`), the `get_redis_client` dependency, and `hash_redis_key`. |
| `logging.py` | `structlog` setup. |
| `rate_limit.py` | `increment_rate_limit` / `reserve_attempt` / `release_attempt` / `seconds_until_reset`, the Redis counters behind every rate limit (see below). |
| `idempotency.py` | Redis storage of idempotency keys: claim, read, save, release (see below). |
| `middlewares.py` | The three HTTP middlewares (`idempotency`, `rate_limit`, `log_requests`) and the helpers that identify a request's user. It only defines them: `main.py` registers them with `app.middleware("http")(...)`, and that registration order decides which wraps which. A test or the evals that redirect something these middlewares use (`async_session_factory`, `ApiTokenRepository`, the idempotency functions) patch it here, not in `src.main`. |

### Generic repository layer

`BaseRepository[ModelT: Base]` provides `get_by_id`, `update`, `delete`, `paginate` and `count_query` — the parts that are genuinely identical across resources. Its `get_by_id` and `delete` know nothing about soft deletes (`delete` is a real `DELETE`); `TodoRepository` and `CategoryRepository` override both.

`SoftDeleteRepository[ModelT]` sits between the two: it extends `BaseRepository` with `prune_soft_deleted(before)`, a single bulk `DELETE` of rows soft-deleted before a date, and `restore(entity)`, which sets the column back to `NULL`. `TodoRepository` and `CategoryRepository` inherit from it; `UserRepository` stays on `BaseRepository`, so the method only exists where it makes sense. The column is the `soft_delete_column` class attribute (`"deleted_at"`), overridden in one line by a model that doesn't follow the convention — Laravel's `const DELETED_AT`. The repository receives the cut-off date; the retention policy lives in the service.

`TodoRepository` keeps its own `get_by_id`/`update` (`# pyrefly: ignore[bad-override]`), because `get_by_id` takes a required keyword-only `owner_id` and `update` an extra `categories` argument, which the shared signature can't express without weakening the "no default" guarantee described under Ownership. `ApprovalRequestRepository` and `AuditLogRepository` override `get_by_id` the same way, with a keyword-only owner filter (`owner_id`, `resource_owner_id`). This divergence is deliberate, not a gap to unify.

`get_all`/`count` stay per-repository, and both go through that repository's private `_apply_filters`, so the page and its total can never drift apart. Plain equality and partial-match filters are passed to `BaseRepository._apply_filter_params` as a `list[FilterParams]` (`NamedTuple(column, value, op: Literal["eq", "ilike"])`) — named and typed, never a dict. Filters that don't fit (`Todo.category_ids` via `.any()`, `User.name` across two columns) are written by hand next to it.

### Uniqueness and race conditions

`Category.name`, `User.email` and `User.pseudo` are unique **case-insensitively**, enforced by functional unique indexes (`uq_partial_categories_name_lower`, `uq_users_email_lower`, `uq_users_pseudo_lower`, all on `lower(column)`), not by a `SELECT` before the `INSERT`. Services deliberately do no pre-check: two concurrent requests would both pass it. They call the repository, catch `IntegrityError`, and raise `ConflictError`.

Consequences to keep in mind:

- `UserService` has two unique columns, so it tells them apart by looking for the **index name** inside `str(e)` and re-raises anything it doesn't recognize. The index names are therefore part of the logic: renaming one in the model without updating the service turns a 409 into an unhandled 500.
- A new unique column follows the same pattern: named functional index in `__table_args__`, hand-checked migration, `IntegrityError` → `ConflictError` in the service, and a `..._with_unrelated_integrity_error_reraises` style unit test.
- The plain `index=True` on those columns is kept alongside the unique one for `ilike` filtering.
- The category index is **partial** (`WHERE deleted_at IS NULL`): a name is unique among live categories only, so the name of a soft-deleted category can be reused by a create or a rename. The model declares both `postgresql_where` and `sqlite_where`; dropping the second would make the SQLite test suite enforce a stricter rule than production.

### Soft deletes

`Todo` and `Category` carry a nullable `deleted_at`; their repositories override `delete()` to set it instead of removing the row. `User` has no soft delete and no delete endpoint — an account is switched off through `is_active`.

There is **no global scope**, unlike Laravel's `SoftDeletes` trait: every query on these two models filters `deleted_at IS NULL` by hand, and a new query must do the same. That includes queries going *through* the relationship (`selectinload`, `.any()`): a soft delete leaves the `todos_categories` rows in place, so the other side has to be filtered too.

A deleted row behaves exactly like a missing one, for admins as well: 404 on `GET`/`PATCH`/`DELETE`, absent from lists and totals, silently dropped when its ID is sent in a todo's `category_ids`.

**Deleted todos can be listed and restored**, through two routes that are the only exception to the rule above. Same ownership rule as the live ones (owner, or any for an admin), through `TodoRepository.get_all_deleted`/`get_deleted_by_id` and `count(is_deleted=True)` — `_apply_filters` takes `is_deleted` so the page and its total still share their filters.

- `GET /todos/deleted` (`PaginatedResponse[DeletedTodoResponse]`, which adds `deleted_at`), most recently deleted first.
- `POST /todos/{todo_id}/restore` answers 404 for a todo that is missing, someone else's, **or not deleted**.
- Categories have no restore: the name of a deleted one may have been reused since (the unique index is partial).

A todo can be restored until the weekly pruning removes it.

**Soft-deleted rows don't stay forever.** Once a week, the `maintenance:prune_soft_deleted` job hard-deletes the todos and categories soft-deleted more than `SOFT_DELETE_RETENTION_DAYS` (30) ago — Laravel's `Prunable` + `model:prune`. Their `todos_categories` rows go with them through the FKs' `ON DELETE CASCADE`, not by hand.

### Authentication

`users` owns the `User` resource; `auth` owns authentication (`POST /login`, `/refresh`, `/logout`, and `get_current_user`).

**Access token** — a JWT whose payload is only `{"sub": str(user.id), "exp": ...}`, valid 5 minutes. `is_admin`/`is_active` are never baked in: `get_current_user` (exposed as `CurrentUserDep`, imported by every other router) re-fetches the `User` on each request and rejects inactive users, so a role change or deactivation applies immediately. The scheme is `HTTPBearer`, not `OAuth2PasswordBearer`: login takes a JSON body (`LoginRequest`), not the OAuth2 form.

**Login takes the same time whether the email exists or not.** `AuthService.login` always runs `verify_password`, against `_DUMMY_PASSWORD_HASH` (a real Argon2 hash computed once at import) when the email is unknown — so response time can't be used to enumerate registered emails.

**Refresh token** — an opaque random string (`secrets.token_urlsafe`), not a JWT, valid `REFRESH_TOKEN_EXPIRE_DAYS` and rotated on every use. Redis only ever stores its SHA-256, under three keys:

```
refresh_token:token:<sha256>          → user_id     # lookup on /refresh
refresh_token:user:<user_id>          → <sha256>    # find and revoke the user's current token
refresh_token:session_start:<user_id> → timestamp   # when this session began; set at login only
```

The first two share the sliding `REFRESH_TOKEN_EXPIRE_DAYS` TTL, reset on every login and refresh.
`session_start` uses its own, longer `REFRESH_TOKEN_ABSOLUTE_MAX_DAYS` TTL and is written only by
`login()` — `refresh()` never touches it.

Rules, all deliberate:

- **One refresh token per user.** Logging in again replaces the previous one, so a second device logs the first one out at its next refresh.
- **Rotation on every `/refresh`.** The presented token is consumed with `GETDEL`, so two concurrent refreshes with the same token can't both succeed, and a new pair is issued.
- **An absolute session lifetime independent of activity.** Rotation alone would let a session renew itself forever as long as the user stays active. `session_start` closes that: checked right after the token lookup and before the DB call, it rejects with the same 401 once it's gone, regardless of how valid the presented token otherwise is.
- **No reuse detection.** Replaying a rotated token just returns 401; it does not revoke the current one.
- `/refresh` re-checks that the user still exists and is active, and returns the same 401 message for every failure.
- `/logout` requires a login session (an API token gets a 403, see below) and deletes all three keys.
- **Changing your password revokes the session too.** `PATCH /users/me/password` calls `revoke_session`, once the password update succeeds.
- SHA-256 rather than Argon2 because the token is high-entropy and must be looked up by its hash. `hash_redis_key` (MD5) is a different thing: it only shortens cache keys and has no security role.

**API tokens** (`src/modules/api_tokens/`) — personal, long-lived tokens for agents, sent in the same `Authorization: Bearer` header. `tda_` + `secrets.token_urlsafe(32)`; only the SHA-256 is stored, and the plain value is returned once, by `POST /api-tokens` (`ApiTokenCreatedResponse` is the only schema with a `token` field — the router builds it from the row plus the plain value, which no row holds).

- **`get_current_user` dispatches on the prefix.** An API token must exist and not be expired (checked in SQL, not in Python), and its owner must be active (401) and **not an admin (403)** — including an owner promoted after creating the token. A token never acts as an admin.
- **A usage, `api` or `mcp`, chosen at creation and never changed** (`ApiTokenCreate.usage`, default `api`, which every token created before it became). An `mcp` token is accepted only with the remote MCP server's service key (`X-MCP-Service-Key`, compared in constant time on bytes against `MCP_SERVICE_KEY`), an `api` token only without it: a stolen `mcp` token is useless straight against the API, which is what the MCP specification's ban on token passthrough is about (`docs/remote-mcp-security-review.md`). `expected_api_token_usage` turns the header into the usage the token must have — a header present but wrong or empty, or any header while no key is configured, gives no usage — and the usage is a condition of the token lookup itself, in `get_current_user` and in the middlewares alike. A mismatch therefore looks exactly like an unknown token: same 401, same message, and the middlewares count it by IP, without even a query when the key is wrong. `MCP_SERVICE_KEY` is optional (no key, no remote server) but refused under 32 bytes or as the `change-me` placeholder. `usage` follows the `jobstatus` `CHECK` pattern (`apitokenusage`): a new value needs a hand-written migration.
- **`SessionUserDep` refuses an API token** (403) on `POST /api-tokens`, `DELETE /api-tokens/{id}`, `PATCH /users/me/email`, `/me/password`, `PATCH /users/{id}`, `POST /logout`, `POST /approval-requests/{id}/approve`, `POST /categories`, `PATCH /categories/{id}` and `GET /audit-logs` (list and detail): an agent can't mint or revoke tokens (its owner's other tokens included), manage the account, log the human out, approve its own requests, read the audit log kept on it, or write a category name — the only text an agent could publish to every other user (see "Known limitations", prompt injection). `GET /api-tokens` stays open to a token: it shows no secret. It doesn't *require* a JWT, it rejects the `tda_` prefix — the auth fixtures override `get_current_user` and send no header. The same prefix check is `IsApiTokenDep`, for a route that behaves differently for an agent rather than refusing it.
- **Creation** refuses admins, then checks the current password (`InvalidPasswordError`, counted in the sensitive-action limit), then caps active tokens at `API_TOKEN_MAX_ACTIVE` (409). The user's row is locked first (`UserRepository.lock`, `SELECT ... FOR UPDATE`) so concurrent creations can't exceed the cap; SQLite ignores `FOR UPDATE`, so a unit test only checks the lock comes before the count.
- **`last_used_at`** is written at most once an hour, by a conditional `UPDATE`.
- Revoking deletes the row. A password change keeps the tokens unless `revoke_api_tokens: true`. `api_tokens:prune_expired` (Sunday 05:00 UTC) deletes tokens expired for more than `API_TOKEN_EXPIRED_RETENTION_DAYS`; never-expiring ones are never pruned.
- `GET /api-tokens` is a plain list, not a `PaginatedResponse`: it is bounded by the active cap plus the retention of expired tokens.

**Roles** — `is_admin`/`is_active` are not settable through `PATCH /users/{id}` (`UserUpdate` has no such fields; Pydantic drops them silently). They change only through `PATCH /users/{id}/active` and `.../admin`, both admin-only and backed by dedicated repository methods (`set_active`/`set_admin`). Both refuse to deactivate or demote the last usable admin, counted as `count(is_admin=True, is_active=True) == 1` — the `is_active=True` part matters, otherwise already-deactivated admins would be counted as available.

**Email** is excluded from `UserUpdate` the same way, for the same reason: changing it is sensitive enough to need its own gate. `PATCH /users/me/email` requires the current password; `PATCH /users/{id}/email` is admin-only and 403s on the admin's own id, forcing them through the password-gated route for themselves too. Both revoke the target's session via `AuthService.revoke_session`.

### Ownership & permissions

`Todo.owner_id` and `Category.created_by_id` are named differently on purpose: the two resources treat their user differently.

**Todos are private.** `TodoRepository.get_all`/`get_by_id`/`count` take an `owner_id: int | None` filter; `TodoService` computes it as `current_user.id`, or for an admin the `owner_id` query parameter of `GET /todos` and `GET /todos/deleted` — `None` when absent, which disables the filter. A regular user's `owner_id` parameter is ignored, not refused. `GET /approval-requests` follows the same rule. Ownership is enforced in one place, the repository's `WHERE`. Requesting someone else's todo therefore raises `NotFoundError`, never `ForbiddenError`: the row simply doesn't match, and a 403 would reveal that the ID exists. `get_by_id`'s `owner_id` is keyword-only with no default because it sits next to `entity_id`, another `int` — a positional call could swap them unnoticed, and a default would let a caller inherit an unfiltered query by accident.

**Categories are shared.** Every authenticated user can list, read and create them — creating and renaming need a login session, not an API token; `created_by_id` gates mutation only. `update_category` requires admin or creator; `delete_category` requires admin — the creator cannot delete their own category. The checks are ordered differently on purpose: `update_category` fetches the row first because it needs `created_by_id` to decide, while `delete_category` checks `is_admin` before touching the repository, since the answer doesn't depend on the row (same order as `UserService.set_user_active`/`set_user_admin`).

`owner_id`/`created_by_id` are never read from the request body. They are passed to `repository.create(...)` as a separate keyword argument taken from `current_user.id`, like `hashed_password` in `UserRepository.create`.

`CategoryService.list_categories`, `get_category_cached` and `get_category_or_404` take no `current_user`. `CurrentUserDep` on `GET /categories` and `GET /categories/{id}` is thus a router-only guard with nothing behind it: on `todos`, removing it would break the `owner_id` computation immediately, but here the endpoint would silently become public. That is why `tests/test_categories.py` has explicit "no token → 401" tests for list/get and `tests/test_todos.py` doesn't need them.

Responses are never filtered field by field according to the viewer's role (`CategoryResponse.created_by_id`, `TodoResponse.owner_id`, `UserResponse.is_admin`/`is_active` are visible to anyone who can see the resource). A resource is either accessible and returned whole, or it is a 401/403/404.

### Category cache

Only categories are cached: they are shared, read by everyone, and rarely written. Todos are per-user and are not cached. All of it lives in `CategoryService`; the repository knows nothing about Redis.

```
category:<id>                            → CategoryResponse JSON               (TTL 8h)
categories:list:<gen>:<md5(skip,limit)>  → {"categories": [...], "total": n}   (TTL 8h)
categories:list:gen                      → integer generation counter          (no TTL)
```

- **A `name` search is never cached**, read or written — each distinct search term would otherwise mint its own cache entry forever, with no bound on how many. `skip` also has an upper bound for the same reason.
- **List invalidation is by generation.** A list can be cached under any combination of `skip`/`limit`, so instead of finding and deleting those keys, every create/update/delete does `INCR categories:list:gen`. Old entries become unreachable and expire on their own.
- **Item invalidation** is a `DEL category:<id>` on update and delete.
- **The cache tolerates a Redis outage.** Every cache read, write and invalidation catches `RedisError` (and, on reads, an unparsable payload), logs a warning (`redis_unavailable` / `cache_corrupted`) and falls back to the database. One exception: a failed read of `categories:list:gen` logs nothing and falls back to generation 0. A cache problem must never turn into a 5xx. The general rate limit does the same (see "Rate limiting"); the refresh-token store and the login/sensitive-action limits have no fallback.
- `get_category_or_404` is the **uncached** read. Use it for anything that then mutates the row or needs an ORM instance (`update_category`, `delete_category`, the existence check of `GET /categories/{id}/todos`); only `GET /categories/{id}` uses `get_category_cached`.

### Cross-module relationships

`todos` and `categories` are many-to-many through `src/modules/todos_categories/`, a plain association `Table` with two `ON DELETE CASCADE` FKs (which only matter for a hard delete — see "Soft deletes"). `todos/models.py` and `categories/models.py` import each other's class only under `TYPE_CHECKING`; the `todos_categories` table is imported normally by both, which is safe because it references `"todos.id"`/`"categories.id"` as strings and imports neither class.

Dependencies between the two modules point one way, `categories → todos` at the router level and `todos → categories` at the schema level, never both for the same layer:

- `todos/schemas.py` imports `CategoryResponse` to build `TodoDetailResponse` (`GET /todos/{id}?include=categories`).
- The reverse is **not** a nested schema, which would make the schema import circular. A category's todos are a separate endpoint, `GET /categories/{id}/todos` → `PaginatedResponse[TodoResponse]`.
- That endpoint is a router depending on another module's *service*: `categories/router.py` imports `TodoServiceDep` and delegates to `TodoService.list_todos(category_ids=[category_id], ...)` instead of re-implementing pagination and ownership filtering. It calls `get_category_or_404` first only to return a real 404 for a missing category, where the todo query alone would return an empty page.

So `categories/schemas.py` must never import from `todos/schemas.py`, and `todos/router.py` must never import from `categories/router.py`.

`approvals` and `todos` follow the same shape: `ApprovalRequestService` takes a `TodoRepository` to freeze and delete completed todos, and `todos/router.py` imports `ApprovalRequestServiceDep` for `POST /todos/delete-completed` — so `approvals/router.py` must never import from `todos/router.py`.

Changing only a todo's categories writes to `todos_categories` alone, so `onupdate` never fires on `todos.updated_at`: `TodoRepository.update` sets `updated_at = func.now()` explicitly whenever categories are passed.

### Optional includes and pagination

`GET /todos/{id}` takes an `include` query param and declares `response_model=TodoResponse | TodoDetailResponse`; the handler builds the right one explicitly with `.model_validate(...)`. The repository method takes a matching `with_<relation>: bool = False` flag that adds a `selectinload`.

List endpoints return `PaginatedResponse[T]`: the service returns `(items, total)` and the router builds the envelope. Their `limit` is typed `LimitQuery` (`src/core/schemas.py`): only 10, 25, 50 or 100 are accepted, default 25. A new list endpoint must use `LimitQuery` rather than its own `ge`/`le` bounds. Multi-value filters on a many-to-many field (`category_ids`) use OR semantics, and `category_ids` is capped at 20 IDs. Free-text filters use `ilike` with `Query(min_length=2)` (5 for `task_name` on `/jobs`, deliberately) so a one-character search can't match everything, and escape `%`/`_` (`BaseRepository.escape_ilike_value`) so a literal one in the search term isn't read as a SQL wildcard.

### Rate limiting

Three independent limits, all fixed-window counters in Redis, all answering 429 with a `Retry-After` header. Thresholds and windows are settings (`*_RATE_LIMIT_*` in `config.py` and `.env.example`).

| Limit | Where | Counted per | What counts |
|---|---|---|---|
| **General** | `rate_limit` middleware (`middlewares.py`) | user ID from a valid access token or usable API token, else client IP | every request except `GET /health` |
| **Login** | `POST /login` | client IP **and** email, separately — either one blocks | failed logins only |
| **Sensitive actions** | `PATCH /users/me/email`, `/me/password`, `POST /api-tokens` | user, one counter shared by all three | wrong current password only (`InvalidPasswordError`) |

```
rate_limit:general:user:<id>  |  rate_limit:general:ip:<ip>
rate_limit:login:ip:<ip>      +  rate_limit:login:email:<email>
rate_limit:sensitive_actions:user:<id>
```

- **The TTL is set with `EXPIRE ... NX` after every `INCR`**, not only when the counter is created: if setting it ever failed once, the next increment sets it, instead of leaving a counter that never expires and blocks forever.
- **`Retry-After` is the blocking counter's remaining TTL**, in seconds (`seconds_until_reset`, never below 1). The middleware sets it on its own `JSONResponse`; the login and sensitive-action dependencies pass it through `TooManyRequestsError(..., headers=...)`, which the `AppBaseError` handler in `main.py` copies onto the response. Any other exception can carry headers the same way.
- **The login and sensitive-action limits reserve the attempt before it is made.** Their dependency (`RateLimitLoginDep`, `SensitiveActionRateLimitDep`) is a `yield` dependency: `reserve_attempt` does the `INCR` first and decides on the value it returns, so parallel requests can't all read a counter still under the threshold. A refused request (429) gives its reservation back on **every** key: no password was tried, and keeping it would let attempts on a blocked email lock out everyone behind the same IP. After the `yield`, the dependency gives the attempt back (`release_attempt`) unless the route raised the one counted error (`UnauthorizedError` / `InvalidPasswordError`). Handlers know nothing of it and services stay unaware of rate limiting. A new limited action follows the same shape.
- **Only failures count** on login and sensitive actions, so a legitimate user isn't throttled by succeeding: a success, any other error, and a 422 all give the attempt back. The 422 matters because the sensitive-action dependency doesn't read the body, so FastAPI runs it — and reserves — even when the body is invalid; the login dependency takes `LoginRequest` and is skipped. A successful login does **not** reset the login counters.
- **A given-back counter can stay at 0.** `release_attempt` deletes the key only when `DECR` goes below zero, i.e. the counter expired while the request ran and `DECR` recreated it at -1 with no TTL. Deleting at 0 would erase an attempt counted in between by another request.
- **The general limit is a middleware**, because it covers every route. It reads the user ID from the JWT signature with no database call; an invalid or expired token falls back to the IP instead of failing. It returns its 429 as a `JSONResponse` itself rather than raising `TooManyRequestsError`.
- **An API token is looked up, never trusted** (`_request_user_id` in `middlewares.py`, `ApiTokenRepository.get_usable_owner_id`): one query applying the same rules as `get_current_user`, so an unknown, expired, admin-owned or deactivated token counts by IP. Counting per token hash without a lookup would hand every random `tda_` value a fresh bucket. The result is computed once per request and kept in `request.state` for the idempotency middleware; if the lookup fails, the request goes on counted by IP.
- **The general limit fails open.** If Redis raises, the middleware logs `redis_unavailable` and lets the request through uncounted, instead of turning every request into a 500 — a deliberate choice, not a gap to close.
- **`GET /health` is never counted**, so a monitoring probe can neither consume a quota nor get a 429.
- **The client IP comes from `X-Forwarded-For` only behind a trusted proxy.** Uvicorn's `ProxyHeadersMiddleware` is wired in `main.py` with `TRUSTED_PROXIES` (a JSON list of IPs or ranges, empty by default: no proxy trusted) and rewrites `request.client.host`, which every IP-based limit reads, the login one in the router included. It must stay the **last middleware declared**, so it wraps the three others. Uvicorn's own handling is switched off with `--no-proxy-headers` (`Dockerfile`, host command): it trusts `127.0.0.1` by default, a second source of truth that the test suite, which never goes through Uvicorn, can't see.
- **`log_requests` must stay registered after `rate_limit` in `main.py`**, so that a 429 from the general limit is still logged and still carries `X-Request-ID`. `test_blocked_request_still_carries_request_id` checks it.

### Idempotency keys

An `Idempotency-Key` header on `POST /todos`, `POST /categories` and `POST /todos/delete-completed` (`IDEMPOTENT_ROUTES` in `middlewares.py`): a retried request gets the stored response back instead of creating a duplicate. **Optional with a JWT, required with an API token** (400 without it): an agent may retry blindly, a human session may not need it. The middleware knows which by `request.state.auth_method`, set by `_request_user_id`; a token that resolves to no user gets the route's 401, never this 400. The `idempotency` middleware makes the HTTP decisions; `src/core/idempotency.py` only stores, and knows nothing of routes or requests.

```
idempotency:<user_id>:<key> → {"status": "in_progress", "request_hash"}                        (IDEMPOTENCY_LOCK_TTL_SECONDS)
                            → {"status": "done", "request_hash", "response_status", "response_body"}  (IDEMPOTENCY_KEY_TTL_MINUTES)
```

- **Keys are scoped per user**, with the same identity as the general rate limit (JWT or API token). Without a valid token the middleware steps aside and the route answers 401. `POST /users` is not covered: email uniqueness already blocks the duplicate.
- **The key is claimed atomically before the route runs** (`SET NX`, short TTL so a crash can't lock it for an hour). A request never runs without holding the claim: a key in progress, or one that vanished between the failed claim and the read, answers 409.
- **Same key, different request → 422**, compared by SHA-256 of the method, the path and the raw body. The method and path matter: a key reused on another route must not replay that route's resource. Empty or longer than 255 characters → 400.
- **Only 2xx responses are stored.** A 4xx depends on state that can change (a taken category name may be freed), so any non-2xx answer or exception releases the key and the retry runs for real. Replays carry `Idempotent-Replayed: true`.
- **Stored only after the commit**, which `DbSessionDep`'s `scope="function"` guarantees: a failed commit answers 500 and stores nothing.
- **Fails closed, unlike the general rate limit.** If Redis is down when claiming, the answer is 503: the client explicitly asked for a no-duplicate guarantee. A Redis failure *after* a success still returns the real 2xx — turning it into an error would push the client to create the duplicate.
- **Registration order in `main.py`: `idempotency`, then `rate_limit`, then `log_requests`, then `ProxyHeadersMiddleware`** (last, see "Rate limiting"), so a replay still counts toward the general limit and is still logged.

### Background jobs

Taskiq, not ARQ (ARQ requires `redis<6`). `src/core/broker.py` holds the `broker` and the `scheduler`; the worker and the scheduler run as separate processes (`worker`/`scheduler` compose services).

- **Exactly one scheduler**: two would send every scheduled job twice. Crons are UTC.
- **`RedisStreamBroker`, not `ListQueueBroker`**, which crashed idle workers every few seconds with redis-py 8. A job is acknowledged only once handled, so a worker dying mid-job hands it to another — **every job must be safe to run twice.** Its Redis keys are the `taskiq:queue` stream and the `taskiq:result` prefix.
- **Jobs live in a `tasks.py` per module**, found by `--fs-discover`, which **both the worker and the scheduler need**: without it the scheduler never imports the job and its schedule silently never fires. A job spanning modules goes into `maintenance`. Each job sets an explicit `task_name` (`"<module>:<job>"`), so moving the file doesn't orphan queued messages.
- **A job opens its own session and commits** (no `DbSessionDep` in the worker), then calls a service. `async_session_factory` is only ever opened at such entry points — `tasks.py`, the tracking middleware, a background task, the HTTP middlewares' API-token lookup (`src/core/middlewares.py`) — never in a service or repository. Each of those modules must be listed in `conftest.py`'s `background_code_uses_test_database`, or its tests write into the real Postgres.
- **Under tests** (`APP_ENV=test`) `broker` is an `InMemoryBroker(await_inplace=True)`: `.kiq()` runs the job, middleware included, before returning.
- **The API starts and stops the broker** in `main.py`'s `lifespan`; the worker never imports `main.py`.
- **No retries.** Taskiq's own result store (`TASK_RESULT_TTL_HOURS`) is only for debugging with `wait_result()`; the `jobs` table is the record.

### Job tracking

```
jobs: task_id (Taskiq id, exposed) · owner_id (NULL for scheduled runs) · task_name · status · result (JSON) · error · started_at · finished_at
```

- **`JobTrackingMiddleware`** (`src/modules/jobs/middleware.py`, a Taskiq middleware, registered on both brokers in `broker.py`) moves every run to `running` then `succeeded`/`failed`. A run with no row yet — a scheduled one — gets a row with no owner. A tracking failure is logged and never blocks the job.
- **`status` is a `StrEnum` stored by value in a `VARCHAR`**, not a native Postgres enum, guarded by a `CHECK` constraint named `jobstatus`, built from `JobStatus` and declared by hand in `__table_args__` rather than implicitly by the `Enum` (`create_constraint=False`): autogenerate proposes dropping an implicit one on every revision. Autogenerate never sees a change to it: **adding a state needs a hand-written migration** that drops and recreates `jobstatus`.
- **`error` holds the exception's class name only.** The owner sees it, and the message can carry internals; responses stay the same for every role (no field filtering, see Ownership).
- **`POST /maintenance/prune`** (admin, 202) creates the `pending` row in the request transaction and sends the job from a `BackgroundTasks`, i.e. **after the commit**, with a `task_id` generated up front. Sent during the handler, a worker could pick the job before the commit, not see the row, create its own ownerless one, and the request's insert would then hit the unique `task_id`. If sending fails, the row is marked `failed`.
- **`GET /jobs`, `GET /jobs/{task_id}`**: a user sees their own jobs, an admin every job including ownerless runs; someone else's job is a 404. The internal `id` is never exposed.
- **`maintenance:prune_old_jobs`** (Sunday 04:00 UTC) deletes rows **created** more than `JOB_RETENTION_DAYS` ago whatever their status, which also clears runs stuck `pending`/`running`.
- `MaintenanceService` reaches todos/categories through their repositories (plain SQL, no rule of theirs applies) but jobs only through `JobService`, which owns `task_id` generation — never `job_service.repository`.

### Approval requests

A bulk action asked by an agent isn't executed: it becomes a request that the human approves or rejects. One action exists, `delete_completed_todos`. The mechanism lives in `src/modules/approvals/`; the action itself stays a todos operation (`TodoRepository.get_completed_ids`/`delete_completed`).

```
approval_requests: owner_id · action · status (pending/approved/rejected) · payload (JSON) · result (JSON) · created_at · expires_at · decided_at
```

- **`POST /todos/delete-completed` answers according to who calls.** A login session executes it directly (200, `{"deleted_count": n}`): approving one's own request would make no sense. An API token gets a `pending` request (202, `ApprovalRequestResponse`), or a 409 when there is no completed todo — an empty request leaves nothing to approve.
- **One pending request per action and user.** A second one answers 409 with the pending request's ID, so the agent points the user to it. The user's row is locked (`UserRepository.lock`) before `get_pending` looks, like the API-token cap. An expired, approved or rejected request doesn't block. `get_pending` uses `scalar_one_or_none` on purpose: two pending requests for one action would be a bug, and it should surface.
- **The human approves exactly what was asked.** The request freezes the IDs in `payload` (`{"todo_ids": [...]}`), computed by the server: the agent sends none, so injected text can't steer the scope. On approval, `delete_completed` re-checks `owner_id`, `is_completed` and `deleted_at`: a todo reopened or deleted since is skipped, a todo completed since isn't in the list, and `result.deleted_count` is what was really deleted.
- **Only the owner decides**, admin included: `decide` takes `owner_id: int`, never `None`. An admin can read anyone's requests, not approve them — and an admin can't hold an API token, so their own requests never come from an agent.
- **`approve` is behind `SessionUserDep`; `reject` is open to a token.** Rejecting executes nothing, and it lets the user cancel a request through the agent.
- **Deciding is one conditional `UPDATE`** (`WHERE status = 'pending' AND expires_at > :now`), checked by its `rowcount`: two simultaneous approvals can't both run the action. The row is read **after** it, and gives the reason of a failed decision — missing → 404, no longer `pending` → 409 already decided, otherwise → 409 expired.
- **`expired` is an API status, not a stored one.** The database keeps `pending` (`ApprovalStatus`, 3 values); a request still `pending` after `expires_at` (`APPROVAL_REQUEST_EXPIRE_DAYS`, 7) has expired. The API's `ApprovalRequestState` (`schemas.py`, 4 values) translates both ways: `ApprovalRequestResponse` shows `expired`, and the list's `status` filter turns `pending`/`expired` into conditions on `expires_at`, with one `now` computed by the service for the page and its total. `_apply_filters` raises `ValueError` for these two without `now`, rather than silently dropping the filter; `approved`/`rejected` don't need it.
- **Pruning**: `approvals:prune_expired` (1st of the month, 03:00 UTC) deletes requests whose `expires_at` is older than `APPROVAL_REQUEST_RETENTION_DAYS` (90), whatever their status — the user keeps about three months of history, decided or not.
- **The action runs in the request's transaction.** If it fails, the switch to `approved` is rolled back and the request stays pending. `payload` is validated on execution (`TypeAdapter(list[int])`, strict `["todo_ids"]`): a corrupted one fails the approval instead of deleting nothing.
- **A new action** needs a value in `ApprovalAction`, its `case` in `_execute_request`, and a hand-written migration of the `approvalaction` `CHECK` constraint (same pattern as `jobstatus`).

### Audit log

Every write on the five audited resources leaves one entry: who, how they authenticated, what, which fields, when. `src/modules/audit/`.

```
audit_logs: actor_id · auth_method (session/api_token) · api_token_id · resource_type · resource_id · resource_owner_id · action · changed_fields (JSON) · created_at
```

- **No foreign key but `actor_id`.** `resource_id`, `resource_owner_id` and `api_token_id` survive the pruning of todos, categories and tokens: the history must outlive what it describes.
- **The actor travels in a context variable** (`audit/context.py`, `current_actor`), never in a parameter: `get_current_user` sets it once authentication succeeds (with the API token's id), `log_requests` resets it at the start of every request, and `AuditLogService.record` reads it. No actor means a system write — and a public registration, which is recorded with an empty actor.
- **Services describe what changed, the audit service fills in who.** A service calls `audit.log_created/updated/deleted/restored/approved/rejected(entity)`; the entity says its own type and owner through the `Auditable` contract (`audit/contracts.py`: `audit_resource_type` ClassVar + `audit_owner_id` property on each model — the owner is who sees the entry: a category's creator, an account itself). `record` is kept for bulk writes, which only have IDs (`.returning(...id)` on the bulk `UPDATE`/`DELETE`). Services take `AuditLogService` built on the request's session, so an entry is committed or rolled back with its write.
- **Only the names of the fields whose value changes**, never the values, so the log isn't a second copy of private data. `changed_fields` (`audit/changes.py`) compares the entity with the new values **before** the repository writes. A relation isn't a column: `TodoService` compares category IDs itself. `log_updated` writes nothing for an empty list: an update that changes nothing isn't recorded. A password change is recorded as `["password"]`.
- **Not recorded**: prunings, scheduled or started by an admin (retention, not actions; `jobs` keeps their counts), jobs, `last_used_at`, login/refresh/logout.
- **Reading** (`GET /audit-logs`, `/{id}`): a user sees the entries about their resources — admins' actions on them included —, an admin every entry (`owner_id` filter, ignored for a user). Filters on `action`, `resource_type`, `resource_id` and `api_token_id` — "what did this token do" before revoking it. Session only: an API token gets a 403.
- **Pruning**: `audit:prune_old` (1st of the month, 01:00 UTC) deletes entries **created** more than `AUDIT_LOG_RETENTION_DAYS` (365) ago.
- `auth_method`, `resource_type` and `action` follow the `jobs` `CHECK` pattern: a new value needs a hand-written migration. A new audited model implements `Auditable` and gets its factory's `AuditLogService`.

### MCP server

`mcp_server/` (top level, outside `src/`) exposes the API to an agent as MCP tools. It is **a plain HTTP client of the API** with API tokens and **never imports `src/`**: an agent must go through the same permissions, rate limits and idempotency as any client. It is built on `MCPServer`, with `mcp>=2.3`. `MCP_TRANSPORT` picks one of two modes, with the same tools:

- **`stdio`** (default): launched locally by the agent, for one user, with that user's `api` token.
- **`http`**: a remote server shared by many agents, each sending its own `mcp` token. The server proves itself to the API with its service key. The threat model and its decisions are in `docs/remote-mcp-security-review.md`.

Files:

- `config.py`: read from the environment only, never instantiated at import (`__main__.py` builds it). A `model_validator` gives each mode exactly its secret: `stdio` requires `TODO_API_TOKEN` and refuses `MCP_SERVICE_KEY` (a local install never holds the key), `http` requires the key (32 bytes at least) and refuses a token of its own. `TODO_API_TOKEN` must start with `tda_`, a prefix duplicated from `security.py`, since `src/` can't be imported.
- `api_client.py`: **no user's token is ever stored on the shared `httpx` client.** Each `request()` takes the caller's `token` and `client_ip`; the configured token is only a fallback, which can't exist in `http` mode, so a tool that forgot its token gets a `ToolError` and nothing reaches the API — never somebody else's identity. The service key is the one default header (`http` only), under a header name duplicated from `security.py`; the caller's IP goes out as `X-Forwarded-For`. Any API error becomes a `ToolError` `"API error <status>: <detail>"` (a 422 detail list is flattened to `field.path: message`), an unreachable API `"API unreachable (<exception>)"`.
- `server.py`: `build_mcp_server(api)`, tools defined inside it so they close over `api` (tests pass a client on `ASGITransport`). **Every tool takes `ctx: Context` first** and passes `_caller(ctx)` to the API. `_caller` reads the token and IP from `ctx.request_context.request`, the HTTP request that carried this tool call (`None` in stdio, hence the fallback). Not a `ContextVar`: the SDK may run a tool outside the HTTP request's task.
- `http_app.py`: `build_http_app(server, settings)` wraps the SDK's streamable HTTP app. **Stateless** (no MCP session, so none to hijack: every request carries its own authentication) with JSON responses. `require_api_token` refuses anything but `Bearer tda_...` with a 401 before the MCP protocol, `initialize` included; the API decides whether the token is valid. `TransportSecuritySettings` is always passed, from `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` (localhost by default): the SDK enables its DNS rebinding protection by itself for a localhost host only, never for `0.0.0.0`. `ProxyHeadersMiddleware` is added last, so it wraps the rest, and trusts `MCP_TRUSTED_PROXIES` only. `__main__.py` runs it with `uvicorn.run(..., proxy_headers=False)`, for the same reason as the API's `--no-proxy-headers`.
- **Tool names, docstrings and `Field` descriptions are the agent's interface**, in English. Their constraints mirror the API schemas (`max_length`...) and must follow them.
- 14 tools. `delete_todo` deletes **one** todo directly (a soft delete), and `list_deleted_todos`/`restore_todo` undo it. `delete_completed_todos` only creates an approval request (see "Approval requests"); `list_approval_requests`/`get_approval_request` let the agent follow it, `reject_approval_request` cancels it when the user asks, and **no tool approves** — the API would refuse a token anyway. Categories are read-only (`list_categories`, `get_category`): the API refuses a token on creation and update, and deletion is admin-only. `create_todo` and `delete_completed_todos` require an `idempotency_key` (the API requires one with an API token) that the agent reuses on retry. `update_todo` never touches completion — that's `complete_todo` — and clears the description through `clear_description: bool`, because an agent can't reliably send an empty string; `None` means "leave unchanged".
- `INSTRUCTIONS` says that titles, descriptions and category names are user data, never instructions, and that bulk actions wait for a human — the agent must say so rather than claim the todos are deleted, and must not work around it one todo at a time. Keep it in step with the tools.
- In stdio mode, stdout is the protocol channel: nothing in `mcp_server/` may `print`.
- `tests/test_mcp_server.py` calls the tools against the app through `Client(server)`, in memory, so `ctx.request_context.request` is `None` there and the stdio token applies. Each test opens its own `Client`.
- `tests/test_mcp_http.py` drives `build_http_app` with hand-written JSON-RPC `tools/call` requests, pinned to protocol version `2025-06-18`: the latest one requires an envelope in `_meta`. It runs the app's lifespan itself (`app.router.lifespan_context`), which starts the SDK's session manager and which `ASGITransport` doesn't run. A fake API (`httpx.MockTransport`) shows what each call sends; two tests go through the real API to check the `mcp`/`api` rule end to end.

### MCP evals

`evals/` runs a real Claude model as an agent on the 14 MCP tools and checks what it did: about ten scenarios on tool use, honesty about approval, and prompt-injection resistance. A manual command, never in CI: every run calls the paid API. Each scenario runs `RUNS` (5) times and the command prints a pass rate per scenario. The agent is `claude-sonnet-5-5` at low effort, the judge `claude-haiku-5-5`.

- **`evals/__init__.py` sets the environment** (dummy JWT secret, `APP_ENV=test`, no debug) because it runs before any module of the package, whatever the import order. In `app.py` it ran too late: Ruff sorts `evals.agent`, which imports `src.main`, before `evals.app`.
- **`eval_app()` redirects sources, it overrides no dependency**: `async_session_factory` (in `src.core.database` and `src.core.middlewares`) and `redis_client` point at an in-memory SQLite (`StaticPool`) and a fakeredis. Requests therefore commit as in production, unlike the test override, and what one tool call writes is there for the next. It yields the session factory, the agent's user id and its API token, and puts the originals back in a `finally`: the harness tests run it inside pytest.
- **The agent loop is hand-written** (`agent.py`), with no `anthropic[mcp]` extra: its helpers target the older `ClientSession` API, not `mcp>=2`. MCP tools are converted to Anthropic tool dicts, the server's `INSTRUCTIONS` are the system prompt, behind a `cache_control` breakpoint that covers the tools too. A failed tool goes back to the model with `is_error`. `anthropic_client` is a parameter so tests can pass a fake.
- **Checks are deterministic first.** A scenario is `name`, `prompt`, an optional `setup` (writes through `Repos`, never commits — the runner does, before the agent starts) and a `check` that reads the database and `run.tool_calls` — and `run.final_text` for data the reply must name, such as todo titles — and returns failure messages. `judge_criterion` is only for what lives in the wording of the reply (says the deletion awaits approval, warns about an injected text): never a substring check on the reply's phrasing.
- **The judge** (`judge.py`) uses `messages.parse` with a Pydantic `Verdict`, wraps the reply in `<reply>` tags and tells the model never to follow instructions inside it, since the reply quotes the injected texts. An empty reply fails without a call.
- **`WRITE_TOOLS` must list every MCP tool that changes something**: some read-only scenarios fail on any of them. A new write tool goes there too.
- `main()` silences both the `httpx` and `httpx2` loggers (the second is the Anthropic SDK's).
- **`tests/test_evals.py`** runs the harness with `FakeModel`, which plays scripted tool calls and a scripted verdict: an ideal run must pass every scenario, a do-nothing run must fail every scenario that asked for something. A new scenario needs its entry in `IDEAL_RUNS` (a test enforces it); the ids in it rely on each setup creating its rows in a fixed order on a fresh database.
- The first full pass (2026-10-23) scored 5/5 everywhere: the scenarios are a regression baseline for changes to the tools, `INSTRUCTIONS` or the model, not a test of the model's limits.

### Logging and request IDs

`structlog` renders JSON. The `log_requests` middleware (`src/core/middlewares.py`) clears the context vars, binds a `request_id`, logs one `request_completed` event per request, and echoes the ID in the `X-Request-ID` response header. An incoming `X-Request-ID` is reused only if it is at most 64 characters, otherwise a UUID is generated. Never pass the `request_id` around by hand.

## Known limitations

Accepted for now; don't "fix" them as a side effect of other work, and don't describe the system as if they were solved.

- **Cache invalidation runs before the commit.** `_invalidate_cache` is called inside the service, but the transaction commits when `get_db_session` exits. A concurrent read in that window can re-cache the pre-update row for up to 8 hours.
- **Request logs are off outside debug.** The log level is `DEBUG` when `settings.DEBUG` is true and `WARNING` otherwise, so `request_completed` (info) is only emitted in debug mode.
- **Logout does not revoke the access token**, and neither do a password or email change: `revoke_session` only removes the refresh token. The access token stays valid until it expires (5 minutes at most).
- **The published image is a dev image.** The `Dockerfile` starts uvicorn with `--reload` and runs as root, and that is what CI pushes to GHCR.
- **Registered emails can be enumerated through registration.** `POST /users` answers `409` for an email that is already taken. `/login` no longer leaks this (same message, same timing), but the 409 itself does: closing it means answering identically whether or not the email exists and confirming by email.
- **The last-admin guard is not atomic.** `set_user_active`/`set_user_admin` count the active admins, then write. Two admins demoting or deactivating each other at the same instant both pass the check and leave no admin.
- **An idempotent replay doesn't recheck the account (JWT only).** For a JWT the middleware reads the user from the signature only, so a replay returns the stored response without `get_current_user`'s `is_active` check: a deactivated user still gets their own stored 201 until their access token expires (5 minutes at most), while every route already answers 401. An API token's lookup does check the account.
- **A request that outlives the idempotency lock can run twice.** The claim expires after `IDEMPOTENCY_LOCK_TTL_SECONDS` even if the request is still running, so a retry can claim the key and run the route in parallel. The first request's `save_idempotent_response`/`release_idempotency_key` then overwrite or delete the second one's claim, since neither checks the claim is still its own. Fixing it means an ownership token stored at claim time and checked atomically on save and release.
- **An API token is looked up in the database before the general rate limit decides.** Even a made-up `tda_` value already blocked by its IP's counter costs one query on the unique hash index. Cheap today; if it ever matters, check the IP counter before the lookup, or remember unknown token hashes in Redis.
- **The `deny` rules of `.claude/settings.json` are a guard against accidental reads, not a barrier.** They stop Claude Code's file tools and the shell commands it recognises from reading `.env`, `.env.mcp` and `.env.evals`, but not a command that reads them without naming them (`grep -r`, a Python script, `uv run --env-file`). The Claude Code sandbox would make it a real barrier, at the cost of approvals for Docker and network commands; declined for a learning project. The secret that costs money is the Anthropic key in `.env.evals`: a spending limit on it in the Anthropic console bounds a leak.
- **No maximum request body size.** Neither the API nor the remote MCP server's own routes cap it before reading: a 20 MB body sent without authentication to `POST /login` is read whole into memory, then refused with a 422, and the idempotency middleware reads the whole body too. Only the general rate limit bounds how often. That cap is normally a reverse proxy's job, and there is none.
- **The remote MCP server checks only the token's format, and has no rate limit of its own.** Any `Bearer tda_...` passes `require_api_token`: `tools/list` answers with the 14 tools without calling the API (the list is not secret), and each `tools/call` reaches the API, which refuses an invalid token and counts it on the IP the server forwarded. Locally, with no reverse proxy and `MCP_TRUSTED_PROXIES` empty, that IP is the Docker network's gateway for every agent (checked: `172.30.0.128`), so all invalid-token traffic through the server shares one IP counter; valid tokens are still counted per user. Likewise, every request sent from the host straight to the API arrives from Docker Desktop's gateway (`192.168.65.1`), so all IP-based limits are shared by every local client.
- **Prompt injection through stored text can only be bounded, not prevented.** Todo titles and descriptions, and above all category names — shared, so written by any user — reach the agent's context through the tools. The MCP `INSTRUCTIONS` tell the model to treat them as data, but that is a request to the model, not a guarantee. What limits the damage is structural: a token can't manage the account or act as an admin, every deletion is a reversible soft delete, and a token can't create or rename a category — so an injection can't copy itself to other users' agents, nor publish its victim's private todos in a name everyone reads. The approval of bulk actions is not a hard limit (see below). An injected text can also get a pending request rejected: the user has to ask again.
- **The approval of bulk actions can be bypassed one todo at a time.** `delete_todo` needs no approval, so an agent — mistaken, or steered by an injected text — can reach the effect of `delete_completed_todos` with repeated calls, unfinished todos included, which the approved action excludes. Only the MCP `INSTRUCTIONS` forbid it. Accepted on purpose: what bounds it is the general rate limit and `restore` until the weekly pruning. A daily deletion quota per token would close it.
