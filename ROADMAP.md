# Roadmap

This project serves two learning goals at once: transferring backend skills from PHP/Laravel to
Python/FastAPI, and learning to work as an agentic developer — building software in deliberate
collaboration with AI agents and agent-facing tooling (MCP, evals, ...), not just using an
assistant to type faster.

Items are removed once done, as part of the same PR that finishes them. What's already built
lives in the README's Features section and in `CLAUDE.md`, not here.

1. **Move the middlewares out of `main.py`.** With request logging, the general rate limit and
   idempotency, `main.py` holds three middlewares and their helpers next to the app setup. Move
   them to their own module (e.g. `src/core/middlewares.py`) and keep `main.py` to wiring. The
   storage modules (`rate_limit.py`, `idempotency.py`) stay HTTP-free; only the HTTP decisions
   move. Their declaration order must be kept: it decides which one wraps which.
2. **Remote MCP server.** Serve the MCP server over HTTP, next to the API, so an agent connects by
   URL with a personal API token instead of launching it locally — how companies expose MCP to
   their customers. Only after a dedicated security review of what a remote agent can reach and
   do.
3. **Commit requests in the test database override.** In `conftest.py`, the `get_db_session`
   override yields the session but never commits, and every session shares one SQLite
   connection (`StaticPool`). A request carrying an API token makes the middlewares open their
   own session, whose close rolls that connection back: tests must commit their setup data by
   hand, and writes from earlier requests in the same test are lost. Commit on success and roll
   back on exception, like the real `get_db_session`, so tests behave like production — then
   drop the manual commits and the matching note in `CLAUDE.md`.
