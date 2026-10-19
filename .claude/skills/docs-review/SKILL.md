---
name: docs-review
description: Review CLAUDE.md, README.md and ROADMAP.md against the code, then apply approved changes.
argument-hint: "[full]"
allowed-tools: Bash(git merge-base:*), Bash(git diff:*), Bash(git status:*)
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
2. Delegate the review to the docs-reviewer subagent, naming the mode and, in diff mode,
   passing the docs diff and the changed code files.
3. Show me its list as is, numbered.
4. Wait for my answer: I approve, reject or amend each item by number.
5. Before applying an approved item, re-read the code cited as its evidence. If it does not
   support the finding, tell me instead of applying it. An approved `unverifiable` item is
   checked first and applied only if the check confirms the issue.
6. Apply only the approved items, following the docs' own rules: English, and for CLAUDE.md
   the rule stated at its top (project decisions only). Generic content is removed, not
   reworded. A finished ROADMAP item is deleted, not rewritten.
7. Show `git diff` of the doc files, then stop. Do not commit.
