#!/bin/sh
# 2026-09-08 (operator-requested): the minecraft mega-prompt (battery 1) with mu's
# PRODUCTION context — recall on: MU.md + AGENTS.md + identity kernel — with and
# without the discovery bootstrap added as the system prompt. Arm P = production;
# arm PB = production + bootstrap (--append-system-prompt is additive: recall-on
# sessions carry no daemon prompt). Both arms have `plan` and `web-probe` on PATH
# and registered in the t4c override catalog, so a model that looks for a way to
# verify its game finds one. qwen3.8-27b-nvfp4 on the vllm lane through the
# capture proxy; 40-min cap per run; arms interleaved; graded T0/T1/T3 by grade.sh
# through the browser rig (rig-up.sh first). Scored: discover / t4c / web-probe
# calls from stderr, plus the grade line.
P=${BATTERY_DIR:?scratch dir with testcfg-P/}
H=$(CDPATH= cd "$(dirname "$0")" && pwd)
MU=${MU:-mu}
RES=$P/minecraft-results.log
TASK=$H/../minecraft-clone-prompt.txt
export T4C_CONFIG="$H/t4c.registry.toml" T4C_SNAPSHOT="$P/t4c-snapshot.rkyv"
mkdir -p "$P/runs" "$P/captures"
for rep in ${REPS:-1 2 3}; do
  for arm in ${ARMS:-P PB}; do
    label=mine-$arm-$rep
    ws=$P/runs/$label; rm -rf "$ws"; mkdir -p "$ws"
    pkill -f "vllm-proxy.py 8435"; sleep 1
    python3 "$H/../vllm-proxy.py" 8435 "$P/captures/$label.bin" > "$P/captures/proxy-$label.log" 2>&1 &
    PROXY=$!
    i=0; until curl -s -m 2 -o /dev/null http://127.0.0.1:8435/v1/models; do i=$((i+1)); [ $i -gt 30 ] && break; sleep 1; done
    extra=""; [ "$arm" = PB ] && extra="--append-system-prompt $H/sys-B-bootstrap.txt"
    start=$(date +%s)
    ( cd "$ws" && PATH="$H:$PATH" XDG_CONFIG_HOME=$P/testcfg-P timeout "${RUN_TIMEOUT:-2400}" "$MU" ask --disable-mcp \
        --provider vllm143 --model qwen3.8-27b-nvfp4 --tools "read,write,ls,edit,grep,glob,bash" --bash-yolo --max-turns 0 \
        $extra --prompt-file "$TASK" ) > "$ws.out" 2> "$ws.err"
    rc=$?; wall=$(( $(date +%s) - start ))
    kill $PROXY 2>/dev/null; wait $PROXY 2>/dev/null
    html=$(sh "$H/../resolve-artifact.sh" "$label" 2>/dev/null | head -1); [ -n "$html" ] || html=$(find "$ws" -maxdepth 2 -name '*.html' | head -1)
    calls=$(grep -a '^\[tool\] ' "$ws.err" | awk '{print $2}' | sort | uniq -c | awk '{printf "%s:%s,",$2,$1}')
    t4c=$(grep -a '^\[tool\] bash' "$ws.err" | grep -c 't4c \(find\|help\|run\)')
    probe=$(grep -a '^\[tool\] bash' "$ws.err" | grep -c 'web-probe')
    plan=$(grep -a '^\[tool\] bash' "$ws.err" | grep -c 'plan set')
    grade=$(BATTERY_DIR=$P sh "$H/../grade.sh" "$label" "$html" 2>/dev/null | tail -1)
    echo "RESULT $label rc=$rc wall=${wall}s html=${html:-none} calls=${calls:-none} t4c_find=$t4c web_probe=$probe plan_set=$plan grade: $grade" | tee -a "$RES"
  done
done
echo "MINECRAFT-DONE" >> "$RES"
