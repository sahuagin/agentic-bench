#!/bin/sh
DIFF="$1"; SLUG="$2"
SCORE_WT=/tmp/cbench/orphan-base; CRATE=mu-core; TEST=bench_acceptance_no_orphan_on_parallel_compaction
RCPATH="crates/mu-core/src/context/compaction/heuristic.rs"
cd "$SCORE_WT" || exit 9
git checkout -- . 2>/dev/null; git clean -fdq 2>/dev/null
APPLY=clean
if [ -s "$DIFF" ]; then git apply "$DIFF" 2>/tmp/cbench/$SLUG.applyerr || { git apply --reject "$DIFF" 2>>/tmp/cbench/$SLUG.applyerr; APPLY=partial; }; else APPLY=empty; fi
[ "$APPLY" = empty ] && { echo "SCORE=0 REASON=no_fix APPLY=empty WALL=0"; exit 0; }
python3 - "$RCPATH" /tmp/cbench/orphan-test.txt <<'PY'
import sys
p,tf=sys.argv[1],sys.argv[2]; s=open(p).read()
if 'bench_acceptance_no_orphan_on_parallel_compaction' not in s:
    i=s.index('mod tests {')+len('mod tests {'); s=s[:i]+'\n'+open(tf).read()+s[i:]; open(p,'w').write(s)
PY
T0=$(date +%s)
if ! cargo test -p $CRATE $TEST 2>/tmp/cbench/$SLUG.testerr >/tmp/cbench/$SLUG.testout; then
  grep -q "error\[" /tmp/cbench/$SLUG.testerr && { echo "SCORE=0 REASON=no_compile APPLY=$APPLY WALL=$(($(date +%s)-T0))"; exit 0; }
fi
grep -q "test result: ok" /tmp/cbench/$SLUG.testout 2>/dev/null \
  && echo "SCORE=1 REASON=pass APPLY=$APPLY WALL=$(($(date +%s)-T0))" \
  || echo "SCORE=0.5 REASON=compiles_but_fails APPLY=$APPLY WALL=$(($(date +%s)-T0))"
