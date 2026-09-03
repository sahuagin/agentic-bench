#!/bin/sh
# mu-316wl battery-3: knowledge not in the repo — recall injection as a
# capability-adder. Four nonsense facts planted in mu's memory store; the
# questions are PARAPHRASED (semantic retrieval, not keyword match). A/B:
# mu-mem (non-bare, memory_recall available -> full memory capability) vs
# mu-bare (--bare, no memory). The delta is the capability the memory feature
# adds. qwen on vLLM; MCP off. Sequential.
SP=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
RUNS=$SP/b3-runs
RES=$SP/results-battery3.log
mkdir -p "$RUNS"; : > "$RES"

run() { # $1 cond  $2 fact  $3 rep  $4... mu-args
  cond="$1"; fact="$2"; rep="$3"; shift 3
  lbl="b3-$cond-$fact-$rep"; start=$(date +%s)
  timeout 180 mu ask --disable-mcp --provider vllm143 --model qwen3.8-27b-nvfp4 \
    "$@" --prompt-file "$SP/b3-q/$fact.txt" \
    > "$RUNS/$lbl.out" 2> "$RUNS/$lbl.err"
  echo "RESULT $lbl rc=$? wall=$(( $(date +%s)-start ))s" >> "$RES"
}

for fact in zephyr nimbus quokka vireo; do
  for rep in 1 2 3; do
    run mem  "$fact" "$rep" --tools "read,grep,glob,memory_recall"
    run bare "$fact" "$rep" --bare --tools ""
  done
done
echo "BATTERY3-DRIVER-DONE" >> "$RES"
