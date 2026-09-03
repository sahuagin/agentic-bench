#!/bin/sh
# grade.sh <label> <artifact.html>  — T0 (complete file) / T1 (JS parses) locally,
# T3 (boots, animates, responds to input in a real browser) via web_probe.py.
# One summary line. Needs the rig up (rig-up.sh) and the jail's py311 playwright.
H=$(dirname "$0")
PY=${WEB_PROBE_PY:-/usr/local/bin/python3.11}
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}
label=$1; html=$2
[ -f "$html" ] || { echo "$label T0=FAIL(no-html)"; exit 0; }
size=$(wc -c < "$html" | tr -d ' ')
python3 "$H/t0t1.py" "$html" "/tmp/grade-$label.js"
if node --check "/tmp/grade-$label.js" >/dev/null 2>&1; then printf 'T1=PASS '; else printf 'T1=FAIL '; fi
out=${BATTERY_DIR:-/tmp}/$label.t3.json
if "$PY" "$H/../web_probe.py" "$html" --remote-host "${WEB_PROBE_REMOTE_HOST:-ollama}" --json "$out" > "${out%.json}.txt" 2>&1; then v=PASS; else v=FAIL; fi
python3 "$H/t3line.py" "$out" "$v" "$size" 2>/dev/null || echo "T3=PROBE-FAIL (see ${out%.json}.txt) size=${size}B"
