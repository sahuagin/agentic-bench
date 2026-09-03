#!/bin/sh
# mu-lg8j1 acceptance: rerun battery-1 (minecraft mega-prompt, qwen3.8-27b-nvfp4 on
# the vllm lane, thinking off) with mu carrying the `verify` tool. n=3, sequential
# (owns the GPU lane). Per-run capture proxy on :8435 (testcfg's vllm143).
P=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
MU=${MU:-${MU:-mu}}
TASK="$P/minecraft-clone-prompt.txt"
RES=$P/results.log
for rep in ${REPS:-1 2 3}; do
  label=mine-mu-verify-$rep
  ws=$P/runs/$label; rm -rf "$ws"; mkdir -p "$ws"
  pkill -f "lg8j1/vllm-proxy.py 8435"; sleep 1
  python3 "$P/vllm-proxy.py" 8435 "$P/captures/$label.bin" > "$P/captures/proxy-$label.log" 2>&1 &
  PROXY=$!
  i=0; until curl -s -m 2 -o /dev/null http://127.0.0.1:8435/v1/models; do i=$((i+1)); [ $i -gt 30 ] && break; sleep 1; done
  start=$(date +%s)
  ( cd "$ws" && XDG_CONFIG_HOME=$P/testcfg timeout 2400 "$MU" ask --bare --disable-mcp \
      --provider vllm143 --model qwen3.8-27b-nvfp4 \
      --tools "read,write,ls,edit,grep,glob,bash,verify" --bash-yolo \
      --prompt-file "$TASK" ) > "$ws.out" 2> "$ws.err"
  rc=$?
  end=$(date +%s)
  verify_calls=$(grep -a -c "^\[tool\] verify" "$ws.err")
  html=$(ls "$ws"/*.html 2>/dev/null | head -1)
  echo "RESULT $label rc=$rc wall=$((end-start))s verify_calls=$verify_calls html=${html:-none}" >> "$RES"
  kill $PROXY 2>/dev/null; wait $PROXY 2>/dev/null
done
echo "BATTERY-DONE" >> "$RES"
