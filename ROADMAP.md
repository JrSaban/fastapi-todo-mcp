# Roadmap

This project serves two learning goals at once: transferring backend skills from PHP/Laravel to
Python/FastAPI, and learning to work as an agentic developer — building software in deliberate
collaboration with AI agents and agent-facing tooling (MCP, evals, ...), not just using an
assistant to type faster.

Items are removed once done, as part of the same PR that finishes them. What's already built
lives in the README's Features section and in `CLAUDE.md`, not here.

1. **Audit log.** Who changed what, when, and whether it was a human or an agent — meaningful
   once there's more than one kind of actor writing to the API. Interacts with soft deletes, and must not depend on rows that pruning will eventually remove. It would also tell whether an approval request was rejected by the user or cancelled by the agent.
2. **Evals for the MCP tools.** Not "does it work", but "does an agent actually use these tools correctly" — including the approval-gated one.
3. **Client IP behind a reverse proxy.** The rate limits identify anonymous clients by
   `request.client.host`. Behind a reverse proxy (Nginx, a load balancer, a hosting platform),
   that is the proxy's address, so every anonymous client would share one counter: a single busy
   client would put everyone in 429, and the login IP counter would fill up with everyone's
   failures. Read the real client IP from `X-Forwarded-For`, but only for requests coming from a
   trusted proxy — otherwise any client could choose its own IP. Only matters once the API is
   deployed behind one.
4. **Atomic attempt limits.** The login and sensitive-action limits read the counter before the
   attempt and increment it only after a failure, so requests sent in parallel all read a
   counter still under the threshold and all get through: 40 parallel logins gave 40 × 401 and
   no 429. An attacker gets up to ~100 tries per IP per window (the general limit's ceiling)
   instead of 5. Fix: reserve the attempt first with `INCR`, which is atomic in Redis, and decide
   on the value it returns, before checking the password. To decide when doing it: whether a
   successful attempt is decremented afterwards or simply counts.
5. **Move the middlewares out of `main.py`.** With request logging, the general rate limit and
   idempotency, `main.py` holds three middlewares and their helpers next to the app setup. Move
   them to their own module (e.g. `src/core/middlewares.py`) and keep `main.py` to wiring. The
   storage modules (`rate_limit.py`, `idempotency.py`) stay HTTP-free; only the HTTP decisions
   move. Their declaration order must be kept: it decides which one wraps which.
6. **Remote MCP server.** Serve the MCP server over HTTP, next to the API, so an agent connects by
   URL with a personal API token instead of launching it locally — how companies expose MCP to
   their customers. Only after a dedicated security review of what a remote agent can reach and
   do.
7. **Commit requests in the test database override.** In `conftest.py`, the `get_db_session`
   override yields the session but never commits, and every session shares one SQLite
   connection (`StaticPool`). A request carrying an API token makes the middlewares open their
   own session, whose close rolls that connection back: tests must commit their setup data by
   hand, and writes from earlier requests in the same test are lost. Commit on success and roll
   back on exception, like the real `get_db_session`, so tests behave like production — then
   drop the manual commits and the matching note in `CLAUDE.md`.
