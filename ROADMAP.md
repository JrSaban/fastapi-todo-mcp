# Roadmap

This project serves two learning goals at once: transferring backend skills from PHP/Laravel to
Python/FastAPI, and learning to work as an agentic developer — building software in deliberate
collaboration with AI agents and agent-facing tooling (MCP, evals, ...), not just using an
assistant to type faster.

Items are removed once done, as part of the same PR that finishes them. What's already built
lives in the README's Features section and in `CLAUDE.md`, not here.

## Make the project a deliverable

Goal: someone who doesn't know the author can understand, run, try and judge the project in a
few minutes, from its GitHub page alone. Decided on 2026-11-12, in this order, one branch each:

1. **License and GitHub page.** An MIT `LICENSE` file. On GitHub, by hand: a product
   description instead of "Learning Python & Agentic devs", and topics (FastAPI, MCP, ...).
2. **One-command startup.** `docker compose up` gives a working API: migrations run on startup
   instead of a manual `alembic upgrade head`, a command creates the first admin (today only
   SQL can), and optional demo data so a visitor doesn't land on an empty database.
3. **Production image.** The published image stops being a dev image: non-root, no
   `--reload`, a healthcheck. The compose setup keeps hot-reload for development. This closes
   the "published image is a dev image" known limitation in `CLAUDE.md`.
4. **Product-first README.** It opens on what the project does, with an architecture diagram
   (API, worker, scheduler, MCP server, Postgres, Redis) and a screenshot or GIF of an agent
   using it. The learning story is kept, but moved down into a short "How this was built"
   section: the move from PHP/Laravel, the split of roles with Claude Code, and `CLAUDE.md` as
   an example of steering an agent on a real project.
5. **Version 1.0.0.** Once everything above is in `main`: a `v1.0.0` tag and a GitHub release.

No online demo: deploying and exposing it publicly is out of scope for this project, and may
come with the next one. The screenshot or GIF stands in for it.

