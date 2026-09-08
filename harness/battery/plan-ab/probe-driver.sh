#!/bin/sh
# mu-96ga9 follow-up (2026-09-08): does the discovery bootstrap, ADDED to the
# production context (recall on: MU.md + AGENTS.md + kernel), make the model
# look for knowledge it lacks? Battery-3 recall probes: four facts that exist
# only in the memory store. Arm P = production shape; arm PB = P + the
# bootstrap as the system prompt (--append-system-prompt is additive here
# because recall-on sessions carry no daemon prompt). Default `mu ask` tools
# (read,grep,glob,memory_recall + final_answer; discover is always present).
# Scored from stderr: discover / memory_recall calls; answer vs the planted value.
P=${BATTERY_DIR:?scratch dir with testcfg-P/}
H=$(CDPATH= cd "$(dirname "$0")" && pwd)
MU=${MU:-mu}
RES=$P/probe-results.log
mkdir -p "$P/probes"
for arm in ${ARMS:-P PB}; do
  for fact in ${FACTS:-zephyr nimbus quokka vireo}; do
    label=probe-$arm-$fact
    extra=""; [ "$arm" = PB ] && extra="--append-system-prompt $H/sys-B-bootstrap.txt"
    start=$(date +%s)
    XDG_CONFIG_HOME=$P/testcfg-P timeout "${RUN_TIMEOUT:-240}" "$MU" ask --disable-mcp \
      --provider "${PROVIDER:-vllm143}" --model "${MODEL:-qwen3.8-27b-nvfp4}" \
      --tools "read,grep,glob,memory_recall" $extra \
      --prompt-file "$H/../recall-probes/$fact.txt" > "$P/probes/$label.out" 2> "$P/probes/$label.err"
    rc=$?; wall=$(( $(date +%s) - start ))
    exp=$(python3 -c "print({'zephyr':'88231','nimbus':'5417','quokka':'92','vireo':'73'}['$fact'])")
    calls=$(grep -a '^\[tool\] ' "$P/probes/$label.err" | awk '{print $2}' | sort | uniq -c | awk '{printf "%s:%s,",$2,$1}')
    ans=$(tr '\n' ' ' < "$P/probes/$label.out" | head -c 100)
    grep -q "$exp" "$P/probes/$label.out" && ok=CORRECT || ok=wrong
    echo "RESULT $label rc=$rc wall=${wall}s answer=$ok calls=${calls:-none} text=\"$ans\"" | tee -a "$RES"
  done
done
