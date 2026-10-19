#!/usr/bin/env bash
# Rewords commits of the current branch without changing their content.
#
# Usage: reword.sh <base> <file>
#   <base>  the commit the branch started from (find-parent.sh's `base`)
#   <file>  one line per commit to reword: "<short hash> <new message>"
#
# Messages are never pasted into a shell command: each `exec` line reads its message from a
# file when it runs, so backticks, `$` or quotes in a message stay plain text.
# Refuses when the branch is pushed, the tree is dirty, a backup from an earlier rewording is
# still there, or another local branch is stacked on top (it would stay on the old commits).
set -euo pipefail

base=$1
list=$2
cur=$(git branch --show-current)
backup=backup/$cur

fail() { echo "refused=$1"; exit 1; }
[ -n "$cur" ] || fail "detached HEAD"
[ -z "$(git ls-remote --heads origin "$cur")" ] || fail "$cur is already pushed"
[ -z "$(git status --porcelain)" ] || fail "the tree has uncommitted changes"
! git rev-parse -q --verify "refs/heads/$backup" >/dev/null || fail "$backup already exists"
stacked=$(git for-each-ref --contains HEAD --format='%(refname:short)' refs/heads | grep -vx "$cur" || true)
[ -z "$stacked" ] || fail "stacked on top: $stacked"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
n=0
while read -r hash message; do
    [ -n "$hash" ] || continue
    n=$((n + 1))
    full=$(git rev-parse -q --verify "$hash^{commit}") || fail "unknown commit $hash"
    git merge-base --is-ancestor "$base" "$full" && [ "$full" != "$(git rev-parse "$base")" ] \
        || fail "$hash is not one of the branch's own commits"
    printf '%s\n' "$message" > "$work/msg$n"
    echo "$full $work/msg$n" >> "$work/map"
done < "$list"
[ "$n" -gt 0 ] || fail "no commit to reword"

cat > "$work/editor.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
map=$(dirname "$0")/map
while IFS= read -r line; do
    printf '%s\n' "$line"
    read -r cmd hash _ <<<"$line" || true
    [ "$cmd" = pick ] || continue
    full=$(git rev-parse "$hash")
    msgfile=$(awk -v h="$full" '$1 == h { print $2 }' "$map")
    [ -z "$msgfile" ] || printf 'exec git commit --amend -q -m "$(cat %q)"\n' "$msgfile"
done < "$1" > "$1.new"
mv "$1.new" "$1"
EOF
chmod +x "$work/editor.sh"

git branch "$backup" HEAD
if ! GIT_SEQUENCE_EDITOR="$work/editor.sh" git rebase -q -i "$base"; then
    git rebase --abort || true
    git reset -q --hard "$backup"
    fail "rebase failed, branch restored from $backup (kept)"
fi
if [ -n "$(git diff "$backup" HEAD)" ]; then
    fail "content changed after rewording: compare with $backup (kept)"
fi
git branch -q -D "$backup"
echo "reworded=$n"
git log --format='%h %s' "$base..HEAD"
