#!/bin/sh
# mu-316wl battery-2: context past comfortable. The corpus IS the prompt
# (needles planted at 6/38/64/92% depth); the model recalls all four. Tests
# whether each harness keeps the whole prompt in the model's context as it
# grows toward the window — mu's lean envelope vs cc's, and cc's 200k
# unknown-model assumption vs qwen's real 262k. mu direct to vLLM; cc via a
# minimal /api/hello-answering forward proxy. Sequential — owns the lane.
SP=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
RUNS=$SP/b2-runs
RES=$SP/results-battery2.log
CCCFG=$SP/ccq-config
mkdir -p "$RUNS"
: > "$RES"

# cc forward proxy (answers /api/hello, forwards to vLLM :11435 /v1/messages)
pkill -f cc-wiretap-vllm; sleep 1
( cd "$SP" && nohup python3 tools/cc-wiretap-vllm.py 8438 /dev/null > /tmp/b2-ccproxy.log 2>&1 & )
sleep 2

run_mu() { # $1 size $2 rep
  lbl="b2-mu-$1-$2"; start=$(date +%s)
  timeout 900 mu ask --bare --disable-mcp --provider vllm143 --model qwen3.8-27b-nvfp4 \
    --tools "" --prompt-file "$SP/corpus/prompt-$1.txt" \
    > "$RUNS/$lbl.out" 2> "$RUNS/$lbl.err"
  rc=$?; echo "RESULT $lbl rc=$rc wall=$(( $(date +%s)-start ))s" >> "$RES"
}

run_cc() { # $1 size $2 rep
  lbl="b2-cc-$1-$2"; start=$(date +%s)
  env -u CLAUDECODE DIALOGUE_REWAKE_MAX=0 \
    ANTHROPIC_BASE_URL=http://127.0.0.1:8438 ANTHROPIC_API_KEY=dummy \
    CLAUDE_CONFIG_DIR="$CCCFG" \
    timeout 900 claude -p --model qwen3.8-27b-nvfp4 --dangerously-skip-permissions \
    < "$SP/corpus/prompt-$1.txt" \
    > "$RUNS/$lbl.out" 2> "$RUNS/$lbl.err"
  rc=$?; echo "RESULT $lbl rc=$rc wall=$(( $(date +%s)-start ))s" >> "$RES"
}

for sz in 8000 90000 170000 210000; do
  for rep in 1 2 3; do
    run_mu "$sz" "$rep"
    run_cc "$sz" "$rep"
  done
done

pkill -f cc-wiretap-vllm
echo "BATTERY2-DRIVER-DONE" >> "$RES"
