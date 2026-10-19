---
name: docs-review
description: Review CLAUDE.md, README.md and ROADMAP.md against the code, then apply approved changes.
argument-hint: "[branch]"
allowed-tools: Bash(git diff:*), Bash(git status:*)
disable-model-invocation: true
---

Arguments: $ARGUMENTS

1. Scope. By default the review is full. If the arguments contain `branch`, collect the files
   changed on this branch: `git diff --name-only main...HEAD` plus `git status --short` for
   uncommitted work. If that list is empty, say so and fall back to a full review.
2. Delegate the review to the docs-reviewer subagent, passing the list of changed files
   when there is one.
3. Show me its list as is, numbered.
4. Wait for my answer: I approve, reject or amend each item by number.
5. Before applying an approved item, re-read the code cited as its evidence. If it does not
   support the finding, tell me instead of applying it. An approved `unverifiable` item is
   checked first and applied only if the check confirms the issue.
6. Apply only the approved items, following the docs' own rules: English, and for CLAUDE.md
   the rule stated at its top (project decisions only). A finished ROADMAP item is deleted,
   not rewritten.
7. Show `git diff` of the doc files, then stop. Do not commit.
