#!/bin/sh
# mu-96ga9: plan/act-on-failures A/B, no mu code change.
#   arm A: non-bare mu ask with sys-A.txt (= the daemon's discovery bootstrap + the
#          plan / exit-code sentences) and the `plan` CLI on PATH
#   arm B: plain non-bare mu ask (daemon-injected bootstrap), nothing else
#   arm T: sys-T.txt (bootstrap + the same sentences with NO tool named) and `plan`
#          registered in t4c via $T4C_CONFIG/$T4C_SNAPSHOT — the discovery path
#   arm P: production-shaped: recall ON, MU.md + AGENTS.md injected (testcfg-P),
#          memory off, no bootstrap, `plan` registered in t4c, no prompt file
# Same task for both: task/durparse + prompt.txt; verifier = cargo test run by US
# after the model exits, with tests/ diffed against the template. qwen3.8-27b-nvfp4
# on the vllm lane through the capture proxy on :8435 (testcfg's vllm143).
# Sequential (owns the GPU lane); arms interleaved per rep.
P=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir holding testcfg/ (runs/ captures/ are created)}
H=$(CDPATH= cd "$(dirname "$0")" && pwd)
MU=${MU:-mu}
RES=$P/results.log
TOOLS="read,write,ls,edit,grep,glob,bash"
# Provider/model: the vllm capture lane by default; set ROLE="<role> <rank>" to
# resolve another target through `agent-role` (config, never a literal). A
# non-vllm143 provider runs DIRECT (no proxy; scored from stderr) and, for
# reasoning models, at --thinking low.
if [ -n "${ROLE:-}" ]; then
  set -- $(agent-role $ROLE) || exit 2
  PROVIDER=$1; MODEL=$2; SUFFIX="-$(echo "$MODEL" | tr '/:.' '---')"; THINK="--thinking low"
else
  PROVIDER=vllm143; MODEL=qwen3.8-27b-nvfp4; SUFFIX=""; THINK=""
fi
mkdir -p "$P/runs" "$P/captures"
for rep in ${REPS:-1 2 3}; do
  for arm in ${ARMS:-A B}; do
    label=plan-ab-$arm$SUFFIX-$rep
    ws=$P/runs/$label; rm -rf "$ws"; mkdir -p "$ws"
    cp -R "$H/task/durparse/." "$ws/"
    PROXY=""
    if [ "$PROVIDER" = vllm143 ]; then
      pkill -f "vllm-proxy.py 8435"; sleep 1
      python3 "$H/../vllm-proxy.py" 8435 "$P/captures/$label.bin" > "$P/captures/proxy-$label.log" 2>&1 &
      PROXY=$!
      i=0; until curl -s -m 2 -o /dev/null http://127.0.0.1:8435/v1/models; do i=$((i+1)); [ $i -gt 30 ] && break; sleep 1; done
    fi
    unset T4C_CONFIG T4C_SNAPSHOT; cfg="$P/testcfg"
    case "$arm" in
      A) extra="--append-system-prompt $H/sys-A.txt"; runpath="$H:$PATH" ;;
      # arm T: the plan sentences name NO tool; `plan` is registered with t4c
      # (t4c.registry.toml as the override layer, snapshot in $P) so the model
      # has to find it through the existing discovery path (`t4c find`).
      T) extra="--append-system-prompt $H/sys-T.txt"; runpath="$H:$PATH"
         export T4C_CONFIG="$H/t4c.registry.toml" T4C_SNAPSHOT="$P/t4c-snapshot.rkyv"
         PATH="$runpath" t4c discover > "$P/captures/t4c-discover-$label.log" 2>&1 ;;
      # arm P: production-shaped. Recall ON with the operator's MU.md + AGENTS.md
      # copied into $P/testcfg-P/mu (memory injection off), so the session gets
      # the project files (discover-first directive) and NO bootstrap; `plan`
      # registered with t4c as in arm T; no --append-system-prompt.
      P) extra=""; runpath="$H:$PATH"; cfg="$P/testcfg-P"
         export T4C_CONFIG="$H/t4c.registry.toml" T4C_SNAPSHOT="$P/t4c-snapshot.rkyv"
         PATH="$runpath" t4c discover > "$P/captures/t4c-discover-$label.log" 2>&1 ;;
      *) extra=""; runpath="$PATH" ;;
    esac
    start=$(date +%s)
    ( cd "$ws" && PATH="$runpath" XDG_CONFIG_HOME="$cfg" timeout "${RUN_TIMEOUT:-1200}" "$MU" ask --disable-mcp \
        --provider "$PROVIDER" --model "$MODEL" $THINK --tools "$TOOLS" --bash-yolo --max-turns 0 \
        $extra --prompt-file "$H/prompt.txt" ) > "$ws.out" 2> "$ws.err"
    rc=$?
    wall=$(( $(date +%s) - start ))
    [ -n "$PROXY" ] && { kill $PROXY 2>/dev/null; wait $PROXY 2>/dev/null; }
    ( cd "$ws" && cargo test --offline > "$ws.cargo.log" 2>&1 ); pass=$?
    # tests/ must be semantically untouched; compare with all whitespace stripped so a
    # `cargo fmt` re-wrap (seen with gpt-5.5) does not count as a modification.
    if [ "$(cat "$H"/task/durparse/tests/*.rs | tr -d ' \t\n')" = "$(cat "$ws"/tests/*.rs 2>/dev/null | tr -d ' \t\n')" ]; then tests=intact; else tests=MODIFIED; fi
    if [ -n "$PROXY" ]; then
      NO_SCRUB=1 python3 "$H/../parse-wire.py" "$P/captures/$label.bin" "$P/captures/$label.jsonl" > /dev/null 2>&1
      score=$(python3 "$H/score.py" "$P/captures/$label.jsonl" "$ws.err")
    else
      score=$(python3 "$H/score-stderr.py" "$ws.err")
    fi
    echo "RESULT $label rc=$rc wall=${wall}s cargo_test=$([ $pass -eq 0 ] && echo PASS || echo FAIL) tests=$tests $score" | tee -a "$RES"
  done
done
echo "BATTERY-DONE" >> "$RES"
