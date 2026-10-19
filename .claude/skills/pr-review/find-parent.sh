#!/usr/bin/env bash
# Finds the parent of the current branch and the commit it started from.
#
# The shape of the commit graph can't tell a parent that got new commits from a child created
# earlier: both have diverged from HEAD. The reflog can, because it records where each branch
# was created from:
#   - the branch's own first entry: "branch: Created from <start point>";
#   - when that start point is HEAD, the HEAD entry "checkout: moving from <X> to <branch>"
#     written when the branch was created.
#
# Output, one key=value per line:
#   parent=<branch name, or main>
#   base=<sha of the commit the branch started from: its own commits are base..HEAD>
#   own_commits=<number of commits in base..HEAD>
#   parent_ahead=<commits on the parent since base: above 0, the branch needs a rebase>
#   note=<optional remark>
# Exit status 1 with "unknown=<reason>" when the reflog has no record: ask the user.
set -euo pipefail

cur=$(git branch --show-current)
[ -n "$cur" ] || { echo "unknown=detached HEAD"; exit 1; }
[ "$cur" != main ] || { echo "unknown=on main"; exit 1; }

first=$(git reflog show --date=unix --format='%gd %gs' "refs/heads/$cur" -- 2>/dev/null | tail -1)
created_at=$(sed -n 's/^[^{]*@{\([0-9]*\)}.*/\1/p' <<<"$first")
from=$(sed -n 's/.* branch: Created from \(.*\)$/\1/p' <<<"$first")

if [ "$from" = HEAD ]; then
    from=$(git reflog show --date=unix --format='%gd %gs' HEAD -- | awk -v t="$created_at" -v b="$cur" '
        {
            match($1, /@\{[0-9]+\}/)
            ts = substr($1, RSTART + 2, RLENGTH - 3)
            if (ts >= t && $0 ~ (" checkout: moving from [^ ]+ to " b "$")) x = $5
        }
        END { print x }')
fi

from=${from#refs/heads/}
from=${from#refs/remotes/}
from=${from#origin/}
if [ -z "$from" ] || [ "$from" = "$cur" ]; then
    echo "unknown=no record of where $cur was created from"
    exit 1
fi

if [ "$from" = main ]; then
    parent=main
elif ! git rev-parse -q --verify "refs/heads/$from" >/dev/null; then
    parent=main
    echo "note=$cur was created from $from, which no longer exists locally: assumed merged"
elif git merge-base --is-ancestor "refs/heads/$from" origin/main; then
    parent=main
    echo "note=$cur was created from $from, already merged into main"
else
    parent=$from
fi

if [ "$parent" = main ]; then
    ref=origin/main
else
    ref=refs/heads/$parent
fi
base=$(git merge-base "$ref" HEAD)

echo "parent=$parent"
echo "base=$base"
echo "own_commits=$(git rev-list --count "$base..HEAD")"
echo "parent_ahead=$(git rev-list --count "$base..$ref")"
