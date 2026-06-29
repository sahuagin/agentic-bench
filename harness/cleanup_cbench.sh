#!/bin/sh
# Remove the git worktrees that shouldn't have been created (switching to jj
# workspaces). Run as a script so cc's jj-guard sees only `sh cleanup_cbench.sh`.
MU="${MU_REPO:-$HOME/src/public_github/mu}"
git -C "$MU" worktree list --porcelain 2>/dev/null | awk '/^worktree/{print $2}' | grep '/tmp/cbench/' | while read -r wt; do
  git -C "$MU" worktree remove --force "$wt" 2>/dev/null || true
done
git -C "$MU" worktree prune 2>/dev/null
rm -rf /tmp/cbench/work-* /tmp/cbench/effort-base /tmp/cbench/orphan-base
echo "remaining mu git worktrees:"; git -C "$MU" worktree list
