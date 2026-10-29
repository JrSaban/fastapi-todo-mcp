---
name: pr-description
description: Write the pull request description of the current branch, following the PR template.
allowed-tools: Bash(git fetch -q --prune origin), Bash(git branch --show-current), Bash(git status --short), Bash(git log:*), Bash(git diff:*), Bash(git merge-base:*), Bash(bash .claude/skills/pr-review/find-parent.sh), Bash(curl -s "https://api.github.com/repos/JrSaban/fastapi-todo-mcp/pulls?state=open")
disable-model-invocation: true
---

1. Read `.github/pull_request_template.md` now, every time: it changes. Use exactly its
   current sections, nothing more.
2. The parent and the starting point `P`: git doesn't know where a branch starts, and without
   `P` the description would cover the parent branches too. If `/pr-review` found them earlier
   in this conversation and the branch hasn't changed since, reuse them. Otherwise run
   `git fetch -q --prune origin`, then `bash .claude/skills/pr-review/find-parent.sh`: `base` is
   `P`. If it exits with `unknown=...`, ask me for the parent and wait.
   Collect `git log --format='%h %s' P..HEAD` and `git diff P HEAD`. Read the changed files where the
   diff alone doesn't explain the change. If the tree has uncommitted changes, say the
   description doesn't cover them.
   The description is always about **this branch only** (`P..HEAD`), even on the last sub-task
   of a task: the parent branches have their own PR. There, the doc updates for the whole task
   are one of this branch's changes, and a fix made here for a parent branch's code is described
   as such.
   You may read the parent PRs' descriptions (the `body` field of
   `curl -s "https://api.github.com/repos/JrSaban/fastapi-todo-mcp/pulls?state=open"`) to stay
   consistent with them and avoid repeating them. Don't suggest fixing them.
3. Write the description in **English**, in a single Markdown code block I can paste. The
   guidance below covers the template's sections as of writing; if the template has changed,
   follow it, apply the guidance to the sections that remain, and tell me what differs:
   - **Why**: the problem, in a few sentences, from the user's or the system's point of view.
   - **Changes**: one bullet per behaviour or decision, not per file or per commit. Name routes,
     settings and Redis keys exactly as in the code.
   - **Notes**: only what a reviewer can't see from the diff or the PR header — trade-offs,
     accepted limitations, follow-ups, unrelated changes riding along. A migration, if there is
     one, with `uv run alembic upgrade head`. Never "No migration", never "Stacked on #N" or
     "merge that one first": the base branch already says it. An empty Notes section is fine.
4. Outside the code block, in French: the base branch to select on GitHub (the parent from
   step 2) and a suggested PR title (`type: short description`,
   same rules as a commit).
5. Don't open or edit the PR, and don't push: I do it myself.
