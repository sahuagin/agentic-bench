#!/bin/sh
# score.sh <model-diff-file> <slug> : reset scoring worktree -> apply model diff ->
# inject hidden acceptance test -> build+test. Emits: SCORE=<1|0.5|0> REASON=.. WALL=<s>
DIFF="$1"; SLUG="$2"
SCORE_WT=/tmp/cbench/effort-base; CRATE=mu-core; TEST=bench_acceptance_openai_api_effort
RCPATH="crates/mu-core/src/route_catalog.rs"
cd "$SCORE_WT" || exit 9
git checkout -- . 2>/dev/null; git clean -fdq 2>/dev/null
APPLY=clean
if [ -s "$DIFF" ]; then git apply "$DIFF" 2>/tmp/cbench/$SLUG.applyerr || { git apply --reject "$DIFF" 2>>/tmp/cbench/$SLUG.applyerr; APPLY=partial; }; else APPLY=empty; fi
[ "$APPLY" = empty ] && { echo "SCORE=0 REASON=no_fix APPLY=empty WALL=0"; exit 0; }
python3 - "$RCPATH" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
if 'bench_acceptance_openai_api_effort' not in s:
    t='\n    #[test]\n    fn bench_acceptance_openai_api_effort() {\n        let (levels, default) = super::effort_config("openai_api", &ResolvedModelSettings::default());\n        let levels: Vec<String> = levels.expect("openai_api must have a fallback").iter().map(|s| s.to_string()).collect();\n        assert_eq!(levels, vec!["low", "medium", "high", "xhigh"]);\n        assert!(!levels.iter().any(|l| l == "minimal"));\n        assert_eq!(default.as_deref(), Some("medium"));\n    }\n'
    i=s.index('mod vcbm_effort_tests {')+len('mod vcbm_effort_tests {'); s=s[:i]+t+s[i:]; open(p,'w').write(s)
PY
T0=$(date +%s)
if ! cargo test -p $CRATE $TEST 2>/tmp/cbench/$SLUG.testerr >/tmp/cbench/$SLUG.testout; then
  grep -q "error\[" /tmp/cbench/$SLUG.testerr && { echo "SCORE=0 REASON=no_compile APPLY=$APPLY WALL=$(($(date +%s)-T0))"; exit 0; }
fi
if grep -q "test result: ok" /tmp/cbench/$SLUG.testout 2>/dev/null; then
  echo "SCORE=1 REASON=pass APPLY=$APPLY WALL=$(($(date +%s)-T0))"
else
  echo "SCORE=0.5 REASON=compiles_but_fails APPLY=$APPLY WALL=$(($(date +%s)-T0))"
fi
