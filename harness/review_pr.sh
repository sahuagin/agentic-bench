#!/bin/sh
# review_pr.sh — end-to-end consensus review of a merged mu PR at its before-commit.
# One command = the whole pipeline: resolve before-commit -> worktree -> build prompt
# -> config-driven discovery panel (panel_review.sh) -> parse -> adjudication round.
# Nothing model-specific is hardcoded; models/tools/adjudicator all come from
# ~/.config/mu/agent_roles.toml.
#
# usage: review_pr.sh <pr-number>
set -u
PR="${1:?usage: review_pr.sh <pr-number>}"
MUREPO="${MUREPO:-$HOME/src/public_github/mu}"
SCRIPTS="$HOME/bench-hosted/scripts"
ROLES="${AGENT_ROLES:-$HOME/.config/mu/agent_roles.toml}"
MU="${MU_BIN:-$HOME/src/public_github/mu/target/release/mu}"
TQ="${TQ:-$HOME/.cargo/bin/tq}"; command -v "$TQ" >/dev/null 2>&1 || TQ=tq
OUT="$HOME/bench-hosted/reviews/pr${PR}"; mkdir -p "$OUT"
say(){ printf '\n========== %s ==========\n' "$1"; }

say "1. resolve before-commit for PR #$PR"
# gh is authoritative (handles squash/custom merge messages the git-grep misses);
# git-log grep is the offline fallback.
merge=$(gh pr view "$PR" -R "${GH_REPO:-sahuagin/mu}" --json mergeCommit -q '.mergeCommit.oid' 2>/dev/null)
[ -z "$merge" ] && merge=$(git -C "$MUREPO" log --all --grep "#$PR" --merges --format='%H' | head -1)
[ -z "$merge" ] && merge=$(git -C "$MUREPO" log --all --grep "(#$PR)" --format='%H' | head -1)
[ -z "$merge" ] && { echo "FAIL: no merge commit for #$PR (gh + git both empty)"; exit 2; }
git -C "$MUREPO" cat-file -e "$merge" 2>/dev/null || git -C "$MUREPO" fetch --quiet origin "$merge" 2>/dev/null || true
before=$(git -C "$MUREPO" rev-parse "${merge}^" 2>/dev/null) || { echo "FAIL: cannot resolve ${merge}^ locally"; exit 2; }
echo "merge=$(git -C "$MUREPO" rev-parse --short "$merge")  before=$(git -C "$MUREPO" rev-parse --short "$before")"
echo "subject: $(git -C "$MUREPO" log -1 --format='%s' "$merge")"

say "2. worktree at before-commit"
WT="$HOME/bench-hosted/worktrees/pr${PR}-before"
[ -d "$WT" ] || git -C "$MUREPO" worktree add --detach "$WT" "$before" >/dev/null 2>&1
echo "WT=$WT @ $(git -C "$WT" rev-parse --short HEAD 2>/dev/null)"

say "3. build review prompt"
git -C "$MUREPO" diff "$before" "$merge" > "$OUT/diff.txt"
{ cat "$SCRIPTS/review_prompt_header.txt"
  echo "PR #$PR diff under review:"; echo '```diff'; cat "$OUT/diff.txt"; echo '```'; } > "$OUT/round1.prompt.txt"
echo "prompt=$(wc -c <"$OUT/round1.prompt.txt")b  diff=$(wc -l <"$OUT/diff.txt") lines"

say "4. discovery panel (config-driven tools + ollama warm-up)"
rm -f "$OUT"/r1.* 2>/dev/null
sh "$SCRIPTS/panel_review.sh" "$OUT/round1.prompt.txt" "$OUT/r1" "$WT" 600
cat "$OUT"/r1.*.done 2>/dev/null

say "5. parse discovery verdicts"
python3 "$SCRIPTS/parse_panel.py" "$OUT/r1" | tee "$OUT/r1.parsed.txt"

say "6. adjudication round (verify findings against code)"
nclaims=$(python3 "$SCRIPTS/build_adjudication.py" "$OUT/r1" "$PR" "$OUT/diff.txt" "$OUT/adjudicate.prompt.txt")
if [ "${nclaims:-0}" -gt 0 ]; then
  set -- $(agent-role code_review_adjudicator 0); aprov="$1"; amodel="$2"
  atools=$("$TQ" -o json -f "$ROLES" code_review_adjudicator.ranked | jq -r '.[0].tools // "read,grep"')
  echo "adjudicator=$aprov/$amodel tools=[$atools] on $nclaims claim(s)"
  OPENROUTER_API_KEY=$(tq -f "$HOME/.config/agent/config.toml" -r openrouter.api_key); export OPENROUTER_API_KEY
  ( cd "$WT" && timeout 900 "$MU" ask --bare --provider "$aprov" --model "$amodel" \
      --tools "$atools" --prompt-file "$OUT/adjudicate.prompt.txt" ) > "$OUT/adjudicate.out" 2>"$OUT/adjudicate.err"
  echo "adjudicator exit=$?  out=$(wc -c <"$OUT/adjudicate.out")b"
else
  echo "no findings raised — discovery consensus stands, no adjudication needed"
fi

say "REVIEW COMPLETE — artifacts in $OUT/"
echo "REVIEW_PR_DONE" > "$OUT/COMPLETE"
