#!/bin/sh
# mu-96ga9: plan/act-on-failures A/B, no mu code change.
#   arm A: non-bare mu ask with sys-A.txt (= the daemon's discovery bootstrap + the
#          plan / exit-code sentences) and the `plan` CLI on PATH
#   arm B: plain non-bare mu ask (daemon-injected bootstrap), nothing else
# Same task for both: task/durparse + prompt.txt; verifier = cargo test run by US
# after the model exits, with tests/ diffed against the template. qwen3.8-27b-nvfp4
# on the vllm lane through the capture proxy on :8435 (testcfg's vllm143).
# Sequential (owns the GPU lane); arms interleaved per rep.
P=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir holding testcfg/ (runs/ captures/ are created)}
H=$(CDPATH= cd "$(dirname "$0")" && pwd)
MU=${MU:-mu}
RES=$P/results.log
TOOLS="read,write,ls,edit,grep,glob,bash"
mkdir -p "$P/runs" "$P/captures"
for rep in ${REPS:-1 2 3}; do
  for arm in ${ARMS:-A B}; do
    label=plan-ab-$arm-$rep
    ws=$P/runs/$label; rm -rf "$ws"; mkdir -p "$ws"
    cp -R "$H/task/durparse/." "$ws/"
    pkill -f "vllm-proxy.py 8435"; sleep 1
    python3 "$H/../vllm-proxy.py" 8435 "$P/captures/$label.bin" > "$P/captures/proxy-$label.log" 2>&1 &
    PROXY=$!
    i=0; until curl -s -m 2 -o /dev/null http://127.0.0.1:8435/v1/models; do i=$((i+1)); [ $i -gt 30 ] && break; sleep 1; done
    if [ "$arm" = A ]; then
      extra="--append-system-prompt $H/sys-A.txt"; runpath="$H:$PATH"
    else
      extra=""; runpath="$PATH"
    fi
    start=$(date +%s)
    ( cd "$ws" && PATH="$runpath" XDG_CONFIG_HOME=$P/testcfg timeout "${RUN_TIMEOUT:-1200}" "$MU" ask --disable-mcp \
        --provider vllm143 --model qwen3.8-27b-nvfp4 --tools "$TOOLS" --bash-yolo --max-turns 0 \
        $extra --prompt-file "$H/prompt.txt" ) > "$ws.out" 2> "$ws.err"
    rc=$?
    wall=$(( $(date +%s) - start ))
    kill $PROXY 2>/dev/null; wait $PROXY 2>/dev/null
    ( cd "$ws" && cargo test --offline > "$ws.cargo.log" 2>&1 ); pass=$?
    if diff -r "$H/task/durparse/tests" "$ws/tests" > /dev/null 2>&1; then tests=intact; else tests=MODIFIED; fi
    NO_SCRUB=1 python3 "$H/../parse-wire.py" "$P/captures/$label.bin" "$P/captures/$label.jsonl" > /dev/null 2>&1
    echo "RESULT $label rc=$rc wall=${wall}s cargo_test=$([ $pass -eq 0 ] && echo PASS || echo FAIL) tests=$tests $(python3 "$H/score.py" "$P/captures/$label.jsonl" "$ws.err")" | tee -a "$RES"
  done
done
echo "BATTERY-DONE" >> "$RES"
