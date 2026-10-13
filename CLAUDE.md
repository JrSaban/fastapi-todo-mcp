# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

FastAPI To-Do List API built with a Clean Architecture layering, used as a learning project: the author is an experienced PHP/Laravel developer learning Python/FastAPI/SQLAlchemy by building it. Python 3.12, managed with `uv`. PostgreSQL is the database; Redis holds the category cache, the refresh tokens, the rate-limit counters, the idempotency keys, and the Taskiq job queue and results. A separate MCP server (`mcp_server/`) lets an agent use the API.

Language conventions, all deliberate:

- Module-level docstrings (top of file) and API error messages (`detail`) are in **French**. Several module docstrings carry a Laravel analogy ("Équivalent conceptuel de ... dans Laravel") — keep them, and add one when a new cross-cutting piece has an obvious Laravel counterpart.
- Function, method and class docstrings are in **English**. Many existing ones are still in French from before this rule.
- Test docstrings, `CLAUDE.md`, `README.md` and `CONTRIBUTING.md` are in English.

Branch names, commit messages and PR rules live in `CONTRIBUTING.md`; the PR template is `.github/pull_request_template.md` (Why / Changes / Notes). Planned work is in `ROADMAP.md`: an item is removed from it in the same PR that finishes it.

## Commands

Everything runs through `uv run`.

```bash
docker compose up -d --build            # API on :8000 (hot-reload on ./src) + Postgres :5432 + Redis :6379 + worker + scheduler
uv run uvicorn src.main:app --reload    # API on the host, against the compose Postgres/Redis

uv run taskiq worker src.core.broker:broker --fs-discover --reload   # job worker (queue:work)
uv run taskiq scheduler src.core.broker:scheduler --fs-discover      # scheduler, ONE instance only

uv run --env-file .env.mcp python -m mcp_server   # MCP server over stdio — normally launched by Claude Code from .mcp.json

uv run pytest                                  # full suite
uv run pytest tests/unit/                      # unit tests only
uv run pytest tests/test_todos.py::test_name   # one test

uv run ruff check --fix . && uv run ruff format .

uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

### Environment

`.env` targets a host-run API (`POSTGRES_HOST=localhost`, `REDIS_URL=redis://localhost:6379/0`). `docker-compose.yml` overrides `DATABASE_URL` and `REDIS_URL` so they point at the `db`/`redis` service names — a new connection setting needs both places. `api`, `worker` and `scheduler` share one `x-app` YAML anchor and differ only by `command`/`ports`.

The MCP server's dependencies (`mcp`, `httpx`) live in a separate `mcp` dependency group, installed by default (`[tool.uv] default-groups`) so tests and CI get it, and excluded from the Docker image (`--no-group mcp`). Its token sits in `.env.mcp`, gitignored, loaded by `uv run --env-file`.

`Settings` refuses to start if `JWT_SECRET_KEY` is shorter than 32 bytes or starts with `change-me`, which is exactly what `.env.example` ships. A fresh `cp .env.example .env` therefore crashes at import time until a real secret is set (`openssl rand -hex 32`). Anything that imports `src.core.config` needs the variable, including Alembic — which is why the CI `migrations` job sets it.

### CI

`.github/workflows/ci.yml` runs four independent jobs on every push and PR: `lint`, `format` (`ruff format --check`), `test`, and `migrations` (`alembic upgrade head` against a real Postgres service). The `migrations` job is the only place the migrations themselves, and the Postgres dialect in general, are exercised — the test suite runs on SQLite with `Base.metadata.create_all`, never through Alembic. A fifth job, `docker`, pushes `ghcr.io/<repo>:latest` on pushes to `main` once the other four pass.

### Migrations

`alembic/env.py` imports every module's `models.py` explicitly so autogenerate can see all tables — a new module's models must be added there too. Alembic's default template is not Ruff-compliant (imports, `Union[...]` hints): fix the generated file to match the existing ones in `alembic/versions/`. Review what autogenerate emits for functional indexes (see "Uniqueness" below): the existing migrations declare them by hand with `sa.literal_column("lower(name)")`.

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

`current_user`/`authenticated_client` (plain user) and `current_admin_user`/`authenticated_admin` (admin) create the `User` row directly through `UserRepository` and override `get_current_user` with `lambda: user`, so no JWT is involved. This is the default for permission-matrix tests. Only `test_get_me_with_real_token`/`test_get_me_without_token_returns_401` in `tests/test_users.py` send a real `Authorization: Bearer` header obtained from a real login, to prove the JWT/dependency wiring end to end. `tests/test_auth.py` uses real registration + `/login` for the refresh-token flows, because those need tokens actually issued into Redis.

`authenticated_client` and `authenticated_admin` return the *same* `client` object: the override is a single entry in `app.dependency_overrides`, not a property of the client. Requesting both in one test does not give two identities — whichever fixture is set up last (parameter order in the test signature) wins for the whole test. A test that needs a second user takes the `other_user` fixture, a plain `User` row with no client attached, and creates that user's data through the repository (`TodoRepository(db_session).create(owner_id=other_user.id, ...)`), never through the API.

`test_create_category_concurrent_same_name_no_duplicate` is the model for race-condition tests: it bypasses the HTTP layer, opens two independent sessions from `test_session_factory`, calls the service in both under `asyncio.gather`, and checks the row count from a third session.

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
| `exceptions.py` | `AppBaseError` and its subclasses, generic rather than per-resource, each carrying its `status_code`. A single `@app.exception_handler(AppBaseError)` in `main.py` handles them all, so a new error type is just a subclass with a `status_code`. `InvalidPasswordError(ForbiddenError)` is raised for a wrong current password **only**: it is what routers count toward the sensitive-action limit, so a 403 for any other reason never does. |
| `repository.py` | `BaseRepository` and `SoftDeleteRepository` (below). |
| `broker.py` | The Taskiq `broker` (in-memory under tests, with `JobTrackingMiddleware`) and `scheduler` (see "Background jobs"). |
| `schemas.py` | `PaginatedResponse[T]` and `LimitQuery`. |
| `security.py` | Argon2 password hashing, JWT access tokens, refresh- and API-token generation, `hash_token` (SHA-256, shared by both). `API_TOKEN_PREFIX` is a constant, not a setting: `get_current_user` recognises API tokens by it, so changing it would orphan every token already issued. |
| `redis.py` | The shared async client (`decode_responses=True`, so values come back as `str`), the `get_redis_client` dependency, and `hash_redis_key`. |
| `logging.py` | `structlog` setup. |
| `rate_limit.py` | `get_retry_after` / `increment_rate_limit` / `seconds_until_reset`, the Redis counters behind every rate limit (see below). |
| `idempotency.py` | Redis storage of idempotency keys: claim, read, save, release (see below). |

### Generic repository layer

`BaseRepository[ModelT: Base]` provides `get_by_id`, `update`, `delete`, `paginate` and `count_query` — the parts that are genuinely identical across resources. Its `get_by_id` and `delete` know nothing about soft deletes (`delete` is a real `DELETE`); `TodoRepository` and `CategoryRepository` override both.

`SoftDeleteRepository[ModelT]` sits between the two: it extends `BaseRepository` with `prune_soft_deleted(before)`, a single bulk `DELETE` of rows soft-deleted before a date. `TodoRepository` and `CategoryRepository` inherit from it; `UserRepository` stays on `BaseRepository`, so the method only exists where it makes sense. The column is the `soft_delete_column` class attribute (`"deleted_at"`), overridden in one line by a model that doesn't follow the convention — Laravel's `const DELETED_AT`. The repository receives the cut-off date; the retention policy lives in the service.

`TodoRepository` keeps its own `get_by_id`/`update` (`# pyrefly: ignore[bad-override]`), because `owner_id`/`categories` are required parameters the shared signature can't express without weakening the "no default" guarantee described under Ownership. This divergence is deliberate, not a gap to unify.

`get_all`/`count` stay per-repository, and both go through that repository's private `_apply_filters`, so the page and its total can never drift apart. Plain equality and partial-match filters are passed to `BaseRepository._apply_filter_params` as a `list[FilterParams]` (`NamedTuple(column, value, op: Literal["eq", "ilike"])`) — named and typed, never a dict. Filters that don't fit (`Todo.category_ids` via `.any()`, `User.name` across two columns) are written by hand next to it.

### Uniqueness and race conditions

`Category.name`, `User.email` and `User.pseudo` are unique **case-insensitively**, enforced by functional unique indexes (`uq_partial_categories_name_lower`, `uq_users_email_lower`, `uq_users_pseudo_lower`, all on `lower(column)`), not by a `SELECT` before the `INSERT`. Services deliberately do no pre-check: two concurrent requests would both pass it. They call the repository, catch `IntegrityError`, and raise `ConflictError`.

Consequences to keep in mind:

- `UserService` has two unique columns, so it tells them apart by looking for the **index name** inside `str(e)` and re-raises anything it doesn't recognize. The index names are therefore part of the logic: renaming one in the model without updating the service turns a 409 into an unhandled 500. Both Postgres and SQLite include the index name in the error message, which is what lets the SQLite suite cover this.
- A new unique column follows the same pattern: named functional index in `__table_args__`, hand-checked migration, `IntegrityError` → `ConflictError` in the service, and a `..._with_unrelated_integrity_error_reraises` style unit test.
- The plain `index=True` on those columns is kept alongside the unique one for `ilike` filtering.
- The category index is **partial** (`WHERE deleted_at IS NULL`): a name is unique among live categories only, so the name of a soft-deleted category can be reused by a create or a rename. The model declares both `postgresql_where` and `sqlite_where`; dropping the second would make the SQLite test suite enforce a stricter rule than production.

### Soft deletes

`Todo` and `Category` carry a nullable `deleted_at`; their repositories override `delete()` to set it instead of removing the row. `User` has no soft delete and no delete endpoint — an account is switched off through `is_active`.

There is **no global scope**, unlike Laravel's `SoftDeletes` trait: every query on these two models filters `deleted_at IS NULL` by hand, and a new query must do the same. That includes queries going *through* the relationship (`selectinload`, `.any()`): a soft delete leaves the `todos_categories` rows in place, so the other side has to be filtered too.

A deleted row behaves exactly like a missing one, for admins as well: 404 on `GET`/`PATCH`/`DELETE`, absent from lists and totals, silently dropped when its ID is sent in a todo's `category_ids`. There is no restore endpoint and no way to list deleted rows.

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
- `/logout` requires a valid access token and deletes all three keys.
- **Changing your password revokes the session too.** `PATCH /users/me/password` calls `revoke_session`, once the password update succeeds.
- SHA-256 rather than Argon2 because the token is high-entropy and must be looked up by its hash. `hash_redis_key` (MD5) is a different thing: it only shortens cache keys and has no security role.

**API tokens** (`src/modules/api_tokens/`) — personal, long-lived tokens for agents, sent in the same `Authorization: Bearer` header. `tda_` + `secrets.token_urlsafe(32)`; only the SHA-256 is stored, and the plain value is returned once, by `POST /api-tokens` (`ApiTokenCreatedResponse` is the only schema with a `token` field — the router builds it from the row plus the plain value, which no row holds).

- **`get_current_user` dispatches on the prefix.** An API token must exist and not be expired (checked in SQL: SQLite returns timestamps without a timezone, so Python comparisons would fail in tests), and its owner must be active (401) and **not an admin (403)** — including an owner promoted after creating the token. A token never acts as an admin.
- **`SessionUserDep` refuses an API token** (403) on `POST /api-tokens`, `PATCH /users/me/email` and `/me/password`: an agent can't mint tokens or manage the account. It doesn't *require* a JWT, it rejects the `tda_` prefix — the auth fixtures override `get_current_user` and send no header.
- **Creation** refuses admins, then checks the current password (`InvalidPasswordError`, counted in the sensitive-action limit), then caps active tokens at `API_TOKEN_MAX_ACTIVE` (409). The user's row is locked first (`UserRepository.lock`, `SELECT ... FOR UPDATE`) so concurrent creations can't exceed the cap; SQLite ignores `FOR UPDATE`, so a unit test only checks the lock comes before the count.
- **`last_used_at`** is written at most once an hour, by a conditional `UPDATE`.
- Revoking deletes the row. A password change keeps the tokens unless `revoke_api_tokens: true`. `api_tokens:prune_expired` (Sunday 05:00 UTC) deletes tokens expired for more than `API_TOKEN_EXPIRED_RETENTION_DAYS`; never-expiring ones are never pruned.
- `GET /api-tokens` is a plain list, not a `PaginatedResponse`: it is bounded by the active cap plus the retention of expired tokens.

**Roles** — `is_admin`/`is_active` are not settable through `PATCH /users/{id}` (`UserUpdate` has no such fields; Pydantic drops them silently). They change only through `PATCH /users/{id}/active` and `.../admin`, both admin-only and backed by dedicated repository methods (`set_active`/`set_admin`). Both refuse to deactivate or demote the last usable admin, counted as `count(is_admin=True, is_active=True) == 1` — the `is_active=True` part matters, otherwise already-deactivated admins would be counted as available.

**Email** is excluded from `UserUpdate` the same way, for the same reason: changing it is sensitive enough to need its own gate. `PATCH /users/me/email` requires the current password; `PATCH /users/{id}/email` is admin-only and 403s on the admin's own id, forcing them through the password-gated route for themselves too. Both revoke the target's session via `AuthService.revoke_session`.

### Ownership & permissions

`Todo.owner_id` and `Category.created_by_id` are named differently on purpose: the two resources treat their user differently.

**Todos are private.** `TodoRepository.get_all`/`get_by_id`/`count` take an `owner_id: int | None` filter; `TodoService` computes it as `current_user.id`, or `None` for an admin, which disables the filter. Ownership is enforced in one place, the repository's `WHERE`. Requesting someone else's todo therefore raises `NotFoundError`, never `ForbiddenError`: the row simply doesn't match, and a 403 would reveal that the ID exists. `get_by_id`'s `owner_id` is keyword-only with no default because it sits next to `entity_id`, another `int` — a positional call could swap them unnoticed, and a default would let a caller inherit an unfiltered query by accident.

**Categories are shared.** Every authenticated user can list, read and create them; `created_by_id` gates mutation only. `update_category` requires admin or creator; `delete_category` requires admin — the creator cannot delete their own category. The checks are ordered differently on purpose: `update_category` fetches the row first because it needs `created_by_id` to decide, while `delete_category` checks `is_admin` before touching the repository, since the answer doesn't depend on the row (same order as `UserService.set_user_active`/`set_user_admin`).

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
- **The cache tolerates a Redis outage.** Every cache read, write and invalidation catches `RedisError` (and, on reads, an unparsable payload), logs a warning (`redis_unavailable` / `cache_corrupted`) and falls back to the database. A cache problem must never turn into a 5xx. The general rate limit does the same (see "Rate limiting"); the refresh-token store and the login/sensitive-action limits have no fallback.
- `get_category_or_404` is the **uncached** read. Use it for anything that then mutates the row or needs an ORM instance (`update_category`, `delete_category`, the existence check of `GET /categories/{id}/todos`); only `GET /categories/{id}` uses `get_category_cached`.

### Cross-module relationships

`todos` and `categories` are many-to-many through `src/modules/todos_categories/`, a plain association `Table` with two `ON DELETE CASCADE` FKs (which only matter for a hard delete — see "Soft deletes"). `todos/models.py` and `categories/models.py` import each other's class only under `TYPE_CHECKING`; the `todos_categories` table is imported normally by both, which is safe because it references `"todos.id"`/`"categories.id"` as strings and imports neither class.

Dependencies between the two modules point one way, `categories → todos` at the router level and `todos → categories` at the schema level, never both for the same layer:

- `todos/schemas.py` imports `CategoryResponse` to build `TodoDetailResponse` (`GET /todos/{id}?include=categories`).
- The reverse is **not** a nested schema, which would make the schema import circular. A category's todos are a separate endpoint, `GET /categories/{id}/todos` → `PaginatedResponse[TodoResponse]`.
- That endpoint is a router depending on another module's *service*: `categories/router.py` imports `TodoServiceDep` and delegates to `TodoService.list_todos(category_ids=[category_id], ...)` instead of re-implementing pagination and ownership filtering. It calls `get_category_or_404` first only to return a real 404 for a missing category, where the todo query alone would return an empty page.

So `categories/schemas.py` must never import from `todos/schemas.py`, and `todos/router.py` must never import from `categories/router.py`.

Changing only a todo's categories writes to `todos_categories` alone, so `onupdate` never fires on `todos.updated_at`: `TodoRepository.update` sets `updated_at = func.now()` explicitly whenever categories are passed.

### Optional includes and pagination

`GET /todos/{id}` takes an `include` query param and declares `response_model=TodoResponse | TodoDetailResponse`; the handler builds the right one explicitly with `.model_validate(...)`. The repository method takes a matching `with_<relation>: bool = False` flag that adds a `selectinload`.

List endpoints return `PaginatedResponse[T]`: the service returns `(items, total)` and the router builds the envelope. Their `limit` is typed `LimitQuery` (`src/core/schemas.py`): only 10, 25, 50 or 100 are accepted, default 25. A new list endpoint must use `LimitQuery` rather than its own `ge`/`le` bounds. Multi-value filters on a many-to-many field (`category_ids`) use OR semantics, and `category_ids` is capped at 20 IDs. Free-text filters use `ilike` with `Query(min_length=2)` (5 for `task_name` on `/jobs`, deliberately) so a one-character search can't match everything, and escape `%`/`_` (`BaseRepository.escape_ilike_value`) so a literal one in the search term isn't read as a SQL wildcard.

### Rate limiting

Three independent limits, all fixed-window counters in Redis, all answering 429 with a `Retry-After` header. Thresholds and windows are settings (`*_RATE_LIMIT_*` in `config.py` and `.env.example`).

| Limit | Where | Counted per | What counts |
|---|---|---|---|
| **General** | `rate_limit` middleware in `main.py` | user ID from a valid access token or usable API token, else client IP | every request except `GET /health` |
| **Login** | `POST /login` | client IP **and** email, separately — either one blocks | failed logins only |
| **Sensitive actions** | `PATCH /users/me/email`, `/me/password`, `POST /api-tokens` | user, one counter shared by all three | wrong current password only (`InvalidPasswordError`) |

- **The TTL is set with `EXPIRE ... NX` after every `INCR`**, not only when the counter is created: if setting it ever failed once, the next increment sets it, instead of leaving a counter that never expires and blocks forever.
- **`Retry-After` is the blocking counter's remaining TTL**, in seconds (`seconds_until_reset`, never below 1). The middleware sets it on its own `JSONResponse`; the login and sensitive-action dependencies pass it through `TooManyRequestsError(..., headers=...)`, which the `AppBaseError` handler in `main.py` copies onto the response. Any other exception can carry headers the same way.
- **The login and sensitive-action limits live in the router, not the service.** A dependency (`RateLimitLoginDep`, `SensitiveActionRateLimitDep`) checks the counter before the handler runs; the handler catches the service's exception (`UnauthorizedError` / `ForbiddenError`), increments, and re-raises. Services stay unaware of rate limiting. A new limited action follows the same shape.
- **Only failures count** on login and sensitive actions, so a legitimate user isn't throttled by succeeding. A successful login does **not** reset the login counters.
- **The general limit is a middleware**, because it covers every route. It reads the user ID from the JWT signature with no database call; an invalid or expired token falls back to the IP instead of failing. It returns its 429 as a `JSONResponse` itself rather than raising `TooManyRequestsError`.
- **An API token is looked up, never trusted** (`_request_user_id` in `main.py`, `ApiTokenRepository.get_usable_owner_id`): one query applying the same rules as `get_current_user`, so an unknown, expired, admin-owned or deactivated token counts by IP. Counting per token hash without a lookup would hand every random `tda_` value a fresh bucket. The result is computed once per request and kept in `request.state` for the idempotency middleware; if the lookup fails, the request goes on counted by IP.
- **The general limit fails open.** If Redis raises, the middleware logs `redis_unavailable` and lets the request through uncounted, instead of turning every request into a 500 — a deliberate choice, not a gap to close.
- **`GET /health` is never counted**, so a monitoring probe can neither consume a quota nor get a 429.
- **`log_requests` must stay declared after `rate_limit` in `main.py`**, so that a 429 from the general limit is still logged and still carries `X-Request-ID`. `test_blocked_request_still_carries_request_id` checks it.

### Idempotency keys

An `Idempotency-Key` header on `POST /todos` and `POST /categories` (`IDEMPOTENT_ROUTES` in `main.py`): a retried request gets the stored response back instead of creating a duplicate. **Optional with a JWT, required with an API token** (400 without it): an agent may retry blindly, a human session may not need it. The middleware knows which by `request.state.auth_method`, set by `_request_user_id`; a token that resolves to no user gets the route's 401, never this 400. The `idempotency` middleware makes the HTTP decisions; `src/core/idempotency.py` only stores, and knows nothing of routes or requests.

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
- **Declaration order in `main.py`: `idempotency`, then `rate_limit`, then `log_requests`**, so a replay still counts toward the general limit and is still logged.

### Background jobs

Taskiq, not ARQ (ARQ requires `redis<6`). `src/core/broker.py` holds the `broker` and the `scheduler`; the worker and the scheduler run as separate processes (`worker`/`scheduler` compose services).

- **Exactly one scheduler**: two would send every scheduled job twice. Crons are UTC.
- **`RedisStreamBroker`, not `ListQueueBroker`**, which crashed idle workers every few seconds with redis-py 8. A job is acknowledged only once handled, so a worker dying mid-job hands it to another — **every job must be safe to run twice.**
- **Jobs live in a `tasks.py` per module**, found by `--fs-discover`, which **both the worker and the scheduler need**: without it the scheduler never imports the job and its schedule silently never fires. A job spanning modules goes into `maintenance`. Each job sets an explicit `task_name` (`"<module>:<job>"`), so moving the file doesn't orphan queued messages.
- **A job opens its own session and commits** (no `DbSessionDep` in the worker), then calls a service. `async_session_factory` is only ever opened at such entry points — `tasks.py`, the tracking middleware, a background task — never in a service or repository. Each of those modules must be listed in `conftest.py`'s `background_code_uses_test_database`, or its tests write into the real Postgres.
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

### MCP server

`mcp_server/` (top level, outside `src/`) exposes the API to an agent as MCP tools, over stdio. It is **a plain HTTP client of the API** with a personal API token and **never imports `src/`**: an agent must go through the same permissions, rate limits and idempotency as any client. The `mcp` SDK is 2.x, where `FastMCP` is renamed `MCPServer`.

- `config.py`: `TODO_API_URL`, `TODO_API_TOKEN` (`SecretStr`, refused at startup unless it starts with `tda_`, a constant duplicated from `security.py` since `src/` can't be imported), read from the environment only. Never instantiated at import: `__main__.py` builds it.
- `api_client.py`: adds the token to every request; any API error becomes a `ToolError` `"API error <status>: <detail>"` (a 422 detail list is flattened to `field.path: message`), an unreachable API `"API unreachable (<exception>)"`.
- `server.py`: `build_mcp_server(api)`, tools defined inside it so they close over `api` (tests pass a client on `ASGITransport`).
- **Tool names, docstrings and `Field` descriptions are the agent's interface**, in English. Their constraints mirror the API schemas (`max_length`...) and must follow them.
- 8 tools, **no delete**: deletion is irreversible for the user and is left for the human-approval gate. The create tools require an `idempotency_key` (the API requires one with an API token) that the agent reuses on retry. `update_todo` never touches completion — that's `complete_todo` — and clears the description through `clear_description: bool`, because an agent can't reliably send an empty string; `None` means "leave unchanged".
- Stdout is the protocol channel: nothing in `mcp_server/` may `print`.
- `tests/test_mcp_server.py` calls the tools against the app through `Client(server)`. Each test opens its own `Client`: its task group must be exited in the task that entered it, which an async `yield` fixture doesn't guarantee under pytest-asyncio.

### Logging and request IDs

`structlog` renders JSON. The `log_requests` middleware in `main.py` clears the context vars, binds a `request_id`, logs one `request_completed` event per request, and echoes the ID in the `X-Request-ID` response header. An incoming `X-Request-ID` is reused only if it is at most 64 characters, otherwise a UUID is generated. Any `structlog.get_logger()` call made during a request inherits the `request_id` automatically — don't pass it around by hand.

## Known limitations

Accepted for now; don't "fix" them as a side effect of other work, and don't describe the system as if they were solved.

- **Cache invalidation runs before the commit.** `_invalidate_cache` is called inside the service, but the transaction commits when `get_db_session` exits. A concurrent read in that window can re-cache the pre-update row for up to 8 hours.
- **Request logs are off outside debug.** The log level is `DEBUG` when `settings.DEBUG` is true and `WARNING` otherwise, so `request_completed` (info) is only emitted in debug mode.
- **Logout does not revoke the access token.** It stays valid until it expires (5 minutes at most).
- **The published image is a dev image.** The `Dockerfile` starts uvicorn with `--reload` and runs as root, and that is what CI pushes to GHCR.
- **Registered emails can be enumerated through registration.** `POST /users` answers `409` for an email that is already taken. `/login` no longer leaks this (same message, same timing), but the 409 itself does: closing it means answering identically whether or not the email exists and confirming by email.
- **The last-admin guard is not atomic.** `set_user_active`/`set_user_admin` count the active admins, then write. Two admins demoting or deactivating each other at the same instant both pass the check and leave no admin.
- **An idempotent replay doesn't recheck the account (JWT only).** For a JWT the middleware reads the user from the signature only, so a replay returns the stored response without `get_current_user`'s `is_active` check: a deactivated user still gets their own stored 201 until their access token expires (5 minutes at most), while every route already answers 401. An API token's lookup does check the account.
- **A request that outlives the idempotency lock can run twice.** The claim expires after `IDEMPOTENCY_LOCK_TTL_SECONDS` even if the request is still running, so a retry can claim the key and run the route in parallel. The first request's `save_idempotent_response`/`release_idempotency_key` then overwrite or delete the second one's claim, since neither checks the claim is still its own. Fixing it means an ownership token stored at claim time and checked atomically on save and release.
- **The login and sensitive-action limits can be bypassed in parallel.** The counter is read before the attempt and incremented only after a failure, so simultaneous attempts all pass the check: 40 parallel logins gave 40 × 401 and no 429. Planned fix: `ROADMAP.md`, "Atomic attempt limits".
