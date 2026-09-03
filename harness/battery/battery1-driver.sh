#!/bin/sh
# mu-316wl battery-1 rep driver: minecraft mega-prompt, nothink row reps 2-3
# (rep 1 of each arm already run by hand) + mu think reps. Sequential — this
# script owns the GPU lane while it runs. Proxy rotated per run (per-run
# capture files; the shared-capture lesson from slices 1-4).
SP=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
OLDSP=${OLD_SCRATCH:-/nonexistent}
NLIN=$OLDSP/linux-node/node-v24.18.0-linux-x64/bin
MU=${MU:-mu}
DSH=/home/claude/src/public_github/deepseek-harness-linux/apps/cli/lib/bin.js
TASK=$(cat "$SP/prompts/minecraft-clone-prompt.txt")
RES=$SP/results-battery1.log

rotate_proxy() { # $1 = capture name, $2 = "think" or ""
  pkill -f vllm-proxy.py; sleep 2
  ( cd "$SP" && nohup python3 tools/vllm-proxy.py 8436 "captures/$1.bin" $2 \
      > "captures/proxy-$1.log" 2>&1 & )
  sleep 2
}

rotate_wiretap() { # $1 = capture name
  pkill -f cc-wiretap-vllm; sleep 2
  ( cd "$SP" && nohup python3 tools/cc-wiretap-vllm.py 8438 "captures/$1.bin" \
      >> "captures/ccwiretap.log" 2>&1 & )
  sleep 2
}

grade() { # $1 = workspace; T0/T1 quick grades into the log
  ws=$1
  html=$(ls "$ws"/*.html 2>/dev/null | head -1)
  if [ -z "$html" ]; then echo "T0=FAIL(no html)"; return; fi
  size=$(wc -c < "$html")
  closing=$(tail -c 200 "$html" | grep -c '</html>')
  python3 -c "
import re,sys
html=open('$html').read()
scripts=[s for s in re.findall(r'<script[^>]*>(.*?)</script>', html, re.S) if s.strip()]
open('/tmp/b1-grade.js','w').write('\n;\n'.join(scripts))
"
  if node --check /tmp/b1-grade.js >/dev/null 2>&1; then t1=PASS; else t1=FAIL; fi
  echo "T0=${size}B/closing=$closing T1=$t1 file=$(basename $html)"
}

run_cell() { # $1 label, $2... command (task appended)
  label=$1; shift
  ws="$SP/runs/$label"
  rm -rf "$ws"; mkdir -p "$ws"
  start=$(date +%s)
  ( cd "$ws" && "$@" "$TASK" ) > "$ws.out" 2> "$ws.err"
  rc=$?
  end=$(date +%s)
  echo "RESULT $label rc=$rc wall=$((end-start))s $(grade $ws)" >> "$RES"
}

for rep in 2 3; do
  rotate_proxy "mine-mu-nothink-$rep"
  run_cell "mine-mu-nothink-$rep" timeout 2400 "$MU" ask --bare --provider vllm143-p2 --model qwen3.8-27b-nvfp4 --tools "read,write,ls,edit,grep,glob,bash" --bash-yolo

  rotate_proxy "mine-dsh-nothink-$rep"
  rm -rf "$SP/dsh-home-linux/sessions"/*
  run_cell "mine-dsh-nothink-$rep" env DSH_HOME="$SP/dsh-home-linux" VLLM_PROBE_KEY=dummy PATH="$NLIN:$PATH" timeout 2400 "$NLIN/node" --expose-internals "$DSH" --profile headless

  rotate_wiretap "mine-cc-nothink-$rep"
  run_cell "mine-cc-nothink-$rep" env -u CLAUDECODE DIALOGUE_REWAKE_MAX=0 ANTHROPIC_BASE_URL=http://127.0.0.1:8438 ANTHROPIC_API_KEY=dummy CLAUDE_CONFIG_DIR="$SP/ccq-config" timeout 2400 claude -p --model qwen3.8-27b-nvfp4 --dangerously-skip-permissions
done

# mu thinking-on reps 2-3 (rep 1 = slice 1, old binary, DNF; patched binary now)
for rep in 2 3; do
  rotate_proxy "mine-mu-think-$rep" think
  run_cell "mine-mu-think-$rep" timeout 2400 "$MU" ask --bare --provider vllm143-p2 --model qwen3.8-27b-nvfp4 --tools "read,write,ls,edit,grep,glob,bash" --bash-yolo
done

pkill -f vllm-proxy.py; pkill -f cc-wiretap-vllm
echo "BATTERY1-DRIVER-DONE" >> "$RES"
