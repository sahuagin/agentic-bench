#!/bin/sh
# mu-pcvqx acceptance: battery-3 paraphrased recall probes with prompt-relevant
# injection ON and NO memory_recall tool (--tools ""). Any planted fact reaching
# the wire is pure injection. Requests captured through the local vllm proxy.
P=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
MU=${MU:-${MU:-mu}}
TS=$(date +%s)
CAP=$P/cap-$TS.bin
python3 "$P/vllm-proxy.py" 8435 "$CAP" 2> "$P/runs/proxy-$TS.err" &
PROXY=$!
sleep 1
for f in zephyr nimbus quokka vireo; do
  start=$(date +%s)
  XDG_CONFIG_HOME="$P/testcfg" timeout 240 "$MU" ask --disable-mcp \
    --provider vllm143 --model qwen3.8-27b-nvfp4 --tools "" \
    --prompt-file "$P/b3-q/$f.txt" > "$P/runs/$f.out" 2> "$P/runs/$f.err"
  echo "RESULT $f rc=$? wall=$(( $(date +%s)-start ))s answer=$(tr '\n' ' ' < "$P/runs/$f.out" | head -c 120)"
done
kill $PROXY 2>/dev/null; wait $PROXY 2>/dev/null
NO_SCRUB=1 python3 "$P/parse-wire.py" "$CAP" "$P/cap-$TS.jsonl" >/dev/null 2>&1
echo "CAPTURE $P/cap-$TS.jsonl"
python3 "$P/analyze.py" "$P/cap-$TS.jsonl"
