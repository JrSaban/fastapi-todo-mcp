---
name: docs-review
description: Review CLAUDE.md, README.md and ROADMAP.md against the code, then apply approved changes.
argument-hint: "[full]"
allowed-tools: Bash(git merge-base:*), Bash(git diff:*), Bash(git status:*), Bash(docker compose config -q), Bash(uv sync --frozen --dry-run), Bash(uv run pytest --collect-only -q)
disable-model-invocation: true
---

Arguments: $ARGUMENTS

1. Mode. If the arguments contain `full`, the review is full. Otherwise it is the diff mode,
   an end-of-task check covering every stacked branch since `main`:
   - `git merge-base main HEAD` gives the base commit;
   - `git diff <base> -- CLAUDE.md README.md ROADMAP.md` gives the docs diff, uncommitted
     changes included;
   - `git diff --name-only <base>` plus the untracked files of `git status --short` give the
     changed code files.
   If both are empty, say so and stop: suggest `/docs-review full` instead. If only the docs
   diff is empty, still run the review: the code may need a doc update nobody wrote.
2. Run the setup checks, which have no side effect, exactly as written:
   - `docker compose config -q` — the compose file is valid. Always with `-q`: without it,
     the command prints the resolved configuration, `.env` secrets included;
   - `uv sync --frozen --dry-run` — the dependencies install from `uv.lock` as committed;
   - `uv run pytest --collect-only -q` — the test suite loads (imports, fixtures), without
     running the tests.
   Keep each command's outcome (success, or the error lines). Never run the commands of
   Getting started that start services or touch a database.
3. Delegate the review to the docs-reviewer subagent, naming the mode, passing the setup
   checks' outcomes and, in diff mode, the docs diff and the changed code files.
4. Show me its list as is, numbered.
5. Wait for my answer: I approve, reject or amend each item by number.
6. Before applying an approved item, re-read the code cited as its evidence. If it does not
   support the finding, tell me instead of applying it. An approved `unverifiable` item is
   checked first and applied only if the check confirms the issue.
7. Apply only the approved items, following the docs' own rules: English, and for CLAUDE.md
   the rule stated at its top (project decisions only). Generic content is removed, not
   reworded. A finished ROADMAP item is deleted, not rewritten.
8. Show `git diff` of the doc files, then stop. Do not commit.
