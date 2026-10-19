---
name: pr-review
description: Review the current branch against its parent branch, or the whole task at its last sub-task, then apply approved fixes.
argument-hint: "[final] [no-tests]"
allowed-tools: Bash(git fetch -q --prune origin), Bash(git branch --show-current), Bash(git status --short), Bash(git log:*), Bash(git diff:*), Bash(git merge-base:*), Bash(git for-each-ref:*), Bash(git rev-list --count:*), Bash(git ls-remote --heads origin:*), Bash(curl -s "https://api.github.com/repos/JrSaban/python-agentic-lab/pulls?state=open"), Bash(uv run ruff check .), Bash(uv run ruff format --check .), Bash(uv run alembic heads), Bash(uv run pytest:*)
disable-model-invocation: true
---

Arguments: $ARGUMENTS

The test run of step 4, the clean copy of step 8 and the rewording of step 9 are left out of
`allowed-tools` on purpose: they write, delete or rewrite history, and ask for permission.

A main task is split into sub-tasks, one stacked branch each, and the docs are updated in the
last one only. If the arguments contain `final`, this is that last sub-task: the review covers
the whole task, every parent branch included. Otherwise it covers this branch only.

1. State. `git fetch -q --prune origin`, `git branch --show-current`, `git status --short`.
   On `main`, say so and stop. Uncommitted changes are not part of the review: list them and
   say so.
2. Parent branch. The branches are often stacked, so the parent is not always `main`. Use the
   **local** branches (a parent may have no PR yet, and its local ref may be ahead of origin):
   - candidates: `M`, the output of `git merge-base origin/main HEAD` (where the branch left
     `main`, which stays an ancestor even after `main` moves on), and every branch of
     `git for-each-ref --format='%(refname:short)' refs/heads` except the current one, `main`
     and `backup/*`;
   - keep those for which `git merge-base --is-ancestor <candidate> HEAD` succeeds;
   - the parent is the one with the smallest `git rev-list --count <candidate>..HEAD`;
     `M` wins a tie (a merged branch is behind it, never closer). When `M` is picked, `P` is
     that commit and the base branch is `main`.
   No candidate left: say so and stop. Say which parent you picked and why, and warn when
   `origin/main` is ahead of `M` (`git rev-list --count M..origin/main` > 0): the branch needs
   a rebase.
   If it helps to understand a parent branch's intent, you may read the open PRs' descriptions
   (the `body` field of
   `curl -s "https://api.github.com/repos/JrSaban/python-agentic-lab/pulls?state=open"`; `gh` is
   not installed) and pass the relevant ones to the subagent as context. A description that no
   longer matches the code is not a finding.
3. Collect: `git log --format='%h %s' P..HEAD`, `git diff --stat P...HEAD`, the full
   `git diff P...HEAD`, and whether the branch is pushed (`git ls-remote --heads origin <branch>`).
   In final mode, also:
   - the stack: the step 2 candidates that are ancestors of `HEAD` but **not** of `origin/main`
     (`git merge-base --is-ancestor <candidate> origin/main` fails), ordered by
     `git rev-list --count origin/main..<candidate>`;
   - `git log --format='%h %s' origin/main..HEAD`, `git diff --stat origin/main...HEAD` and the
     full `git diff origin/main...HEAD`.
4. Checks, keeping each outcome (pass, or the error lines):
   - `uv run ruff check .` and `uv run ruff format --check .`;
   - `uv run alembic heads` — more than one head is a failure;
   - unless the arguments contain `no-tests`, the test suite as CI runs it: on a clean copy of
     `HEAD`, without `.env` or local files, so a test relying on them fails here too:
     `S=<scratchpad>/ci-sim; rm -rf "$S" && mkdir -p "$S" && git archive HEAD | tar -x -C "$S" && (cd "$S" && env -i PATH="$PATH" HOME="$HOME" uv run --quiet pytest -q)`
     The `cd` stays inside the parentheses: otherwise every later command runs in the copy.
     Report the last lines (counts, failures).
5. Delegate the review to the pr-reviewer subagent, naming the mode and passing everything from
   steps 1–4.
6. Show me its list, translated into French, keeping its numbering, categories and `file:line`
   references unchanged. Do **not** write the PR description now: it
   comes after the review is settled (`/pr-description`). In final mode, remind me to run
   `/docs-review` once the fixes are in.
7. Wait for my answer: I approve, reject or amend each item by number.
8. In final mode, a fix for a finding from a parent branch is still made on the current branch,
   as a new commit: parent branches have their own review (and may already be pushed), and
   changing them from here would mean restacking every child branch.
   Before applying an approved item, re-read the code it cites. If it doesn't support the
   finding, tell me instead of applying it. An approved `unverifiable` item is checked first:
   for a test suspected not to prove its fix, build a clean copy of `HEAD` as in step 4
   (`git archive HEAD | tar -x`, so no `.env`, `.env.mcp` or other local file comes along),
   revert the fix there and run that test with the same `env -i` command; it must fail.
9. Approved `commit` items (rewording) come **first**, while the tree is still clean and the
   commits are those I reviewed. Only if the branch is **not pushed** and the tree is clean —
   otherwise tell me and skip them:
   - `git branch -f backup/<branch> HEAD`;
   - write `<scratchpad>/seq.sh`, a sequence editor that appends
     `exec git commit --amend -q -m "<new message>"` after each `pick` line whose hash starts
     with, or is a prefix of, the reviewed hash (`git log` and the rebase todo may abbreviate
     differently). Recreate it every time: the scratchpad doesn't survive sessions;
   - `GIT_SEQUENCE_EDITOR=<scratchpad>/seq.sh git rebase -q -i P`;
   - check `git diff backup/<branch> HEAD` is empty and show the new `git log --oneline P..HEAD`;
   - once it is, `git branch -D backup/<branch>`. If it isn't, keep the backup and tell me.
   Messages follow the git-commit skill.
10. Then apply the approved code and test items with Edit/Write, Ruff-compliant and matching the
    surrounding code, and run the targeted tests. Ruff runs at commit time (git-commit skill).
11. Commit fixes only if I ask, through the git-commit skill. Never push.
