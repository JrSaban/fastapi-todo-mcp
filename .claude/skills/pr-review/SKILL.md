---
name: pr-review
description: Review the current branch against its parent branch, or the whole task at its last sub-task, then apply approved fixes.
argument-hint: "[final] [no-tests]"
allowed-tools: Bash(git fetch -q --prune origin), Bash(git branch --show-current), Bash(git status --short), Bash(git log:*), Bash(git diff:*), Bash(git merge-base:*), Bash(git for-each-ref:*), Bash(bash .claude/skills/pr-review/find-parent.sh), Bash(git ls-remote --heads origin:*), Bash(curl -s "https://api.github.com/repos/JrSaban/python-agentic-lab/pulls?state=open"), Bash(uv run ruff check .), Bash(uv run ruff format --check .), Bash(uv run alembic heads), Bash(uv run pytest:*)
disable-model-invocation: true
---

Arguments: $ARGUMENTS

A main task is split into sub-tasks, one stacked branch each, and the docs are updated in the
last one only.
- Without `final`: the review covers **this branch only**.
- With `final` (the last sub-task): it covers **the whole task since `main`**, every parent
  branch included, pushed or not.

Commands that write a file (the diff, the test copy, `reword.txt`) or rewrite history
(`reword.sh`) are left out of `allowed-tools` on purpose: they ask for permission. The two
scripts next to this file are tested; use them rather than redoing their work by hand.

1. State. `git fetch -q --prune origin`, `git branch --show-current`, `git status --short`.
   On `main`, say so and stop. Uncommitted changes are not part of the review: list them and
   say so.
2. Parent and starting point `P`, in both modes (the branch's own commits are `P..HEAD`): run
   `bash .claude/skills/pr-review/find-parent.sh`. Don't work it out from the commit graph: a
   parent that got new commits and a child created earlier look the same there; the script
   reads where the branch was created from in the reflog.
   - It prints `parent`, `base` (that is `P`), `own_commits`, `parent_ahead`, and maybe a
     `note`: tell me the parent and the note.
   - `parent_ahead` above 0: the parent got commits since the branch left it (fixes, or `main`
     moving on). Warn me that the branch needs a rebase; the review still uses `P`, so it
     covers this branch's commits only.
   - `own_commits` is 0: nothing to review, say so and stop.
   - It exits with `unknown=<reason>` (branch fetched from GitHub, renamed, or created too long
     ago): ask me for the parent and wait, then `P` is `git merge-base <parent> HEAD`
     (`origin/main` for `main`).
   If it helps to understand a parent branch's intent, you may read the open PRs' descriptions
   (the `body` field of
   `curl -s "https://api.github.com/repos/JrSaban/python-agentic-lab/pulls?state=open"`; `gh` is
   not installed) and pass the relevant ones to the subagent as context. A description that no
   longer matches the code is not a finding.
3. Collect:
   - `git log --format='%h %s' P..HEAD`, and whether the branch is pushed
     (`git ls-remote --heads origin <branch>`);
   - branch mode: `git diff --stat P HEAD`, and the full diff in a file:
     `git diff P HEAD > <scratchpad>/review.diff`;
   - final mode: `git log --format='%h %s' origin/main..HEAD`, `git diff --stat origin/main...HEAD`,
     and the full diff in a file: `git diff origin/main...HEAD > <scratchpad>/review.diff`.
4. Checks, keeping each outcome (pass, or the error lines):
   - `uv run ruff check .` and `uv run ruff format --check .`;
   - `uv run alembic heads` — more than one head is a failure;
   - unless the arguments contain `no-tests`, the test suite as CI runs it: on a clean copy of
     `HEAD`, without `.env` or local files, so a test relying on them fails here too:
     `S=<scratchpad>/ci-sim; rm -rf "$S" && mkdir -p "$S" && git archive HEAD | tar -x -C "$S" && (cd "$S" && env -i PATH="$PATH" HOME="$HOME" uv run --quiet pytest -q)`
     The `cd` stays inside the parentheses: otherwise every later command runs in the copy.
     Report the last lines (counts, failures).
5. Delegate the review to the pr-reviewer subagent, passing: the mode, the branch and its
   parent (and the rebase warning, if any), the commit list(s), whether the branch is pushed,
   the `--stat`, the path of `review.diff`, the checks' outcomes, and any PR descriptions read.
6. Show me its list, translated into French, keeping its numbering, categories and `file:line`
   references unchanged. Do **not** write the PR description now: it comes after the review is
   settled (`/pr-description`). In final mode, remind me to run `/docs-review` once the fixes
   are in.
7. Wait for my answer: I approve, reject or amend each item by number.
8. Before applying an approved item, re-read the code it cites. If it doesn't support the
   finding, tell me instead of applying it. An approved `unverifiable` item is checked first:
   for a test suspected not to prove its fix, build a clean copy of `HEAD` as in step 4
   (`git archive HEAD | tar -x`, so no `.env`, `.env.mcp` or other local file comes along),
   revert the fix there and run that test with the same `env -i` command; it must fail.
   In final mode, a fix for code from a parent branch is still made on the current branch, as a
   new commit: changing a parent from here would mean restacking every child branch.
9. Approved `commit` items (rewording) come **first**, while the commits are those I reviewed.
   Write `<scratchpad>/reword.txt`, one line per commit: `<short hash> <new message>`
   (messages follow the git-commit skill), then run
   `bash .claude/skills/pr-review/reword.sh P <scratchpad>/reword.txt`. Never reword by hand.
   The script refuses (`refused=<reason>`) when the branch is pushed, the tree is dirty, a
   `backup/<branch>` from an earlier rewording is still there (I decide what to do with it), or
   another local branch is stacked on top. Otherwise it keeps a `backup/<branch>`, rebases,
   checks the content is unchanged, deletes the backup and prints the new log. Show me its
   output; on a refusal, skip the rewording and go on with step 10.
10. Then apply the approved code and test items with Edit/Write, Ruff-compliant and matching the
    surrounding code, and run the targeted tests. Ruff runs at commit time (git-commit skill).
11. Commit fixes only if I ask, through the git-commit skill. Never push.
