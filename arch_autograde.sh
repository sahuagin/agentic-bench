#!/bin/sh
# Wait for the arch-bench full sweep to finish (by PID), then grade + summarize.
# Launched detached so the morning has scored results without a live session.
# Usage: arch_autograde.sh <sweep_pid> <tag>
set -u
SWEEP_PID="$1"
TAG="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/usr/local/bin/python3.11
LOG="$HERE/arch_results/autograde-$TAG.log"

echo "autograde: waiting on sweep PID $SWEEP_PID ($(date))" > "$LOG"
# poll until the sweep process exits
while kill -0 "$SWEEP_PID" 2>/dev/null; do
    sleep 60
done
echo "autograde: sweep done ($(date)); grading raw-$TAG.jsonl" >> "$LOG"

cd "$HERE" || exit 1
# grade (judge uses the local free model; safe to run on the host)
"$PY" arch_score.py "arch_results/raw-$TAG.jsonl" --tag "$TAG" >> "$LOG" 2>&1
echo "autograde: scoring complete ($(date))" >> "$LOG"

# confirm ollama is serving (hard requirement: leave it up) and re-probe
if curl -s -m 10 "${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags" >/dev/null 2>&1; then
    echo "autograde: ollama SERVING ok" >> "$LOG"
else
    echo "autograde: WARNING ollama NOT serving — needs a restart" >> "$LOG"
fi
echo "autograde: finished ($(date))" >> "$LOG"
