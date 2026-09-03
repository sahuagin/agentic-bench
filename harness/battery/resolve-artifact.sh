#!/bin/sh
# resolve-artifact — given a run label, print the path to its produced HTML,
# searching (in order): the run workspace, a trailing-dot sibling (dsh cwd
# quirk), /home/claude/work (models that mkdir their own dir), and finally
# any .html path the run's own log/transcript recorded writing. Phase-2
# finding: models place the artifact unpredictably; grade by resolution,
# never by assuming it sits in the workspace.
SP=${BATTERY_DIR:?set BATTERY_DIR to a scratch dir for runs/captures}
label=$1
ws="$SP/runs/$label"

# 1. workspace (+ trailing-dot sibling)
for d in "$ws" "$ws."; do
  h=$(ls "$d"/*.html 2>/dev/null | grep -v backup | head -1)
  [ -n "$h" ] && { echo "$h"; exit 0; }
done

# 2. common self-made dir
h=$(ls /home/claude/work/*.html 2>/dev/null | head -1)
if [ -n "$h" ]; then
  # only trust it if newer than the run's start
  if [ -f "$ws.start" ] && [ "$h" -nt "$ws.start" ]; then echo "$h"; exit 0; fi
fi

# 2b. models normalize the ugly /compat/linux/tmp/.../-home-claude/<sid>/...
#     scratchpad path into a clean /home/claude/<sid>/... and mkdir it fresh.
mirror="/home/claude/${SP#*/-home-claude/}/runs/$label"
h=$(ls "$mirror"/*.html 2>/dev/null | head -1)
[ -n "$h" ] && { echo "$h"; exit 0; }

# 3. a write path recorded in the run's stderr (mu) or dsh transcript
wp=$(grep -aoE '"(file_?[Pp]ath|path)":"[^"]*\.html' "$ws.err" 2>/dev/null | head -1 | sed 's/.*":"//')
[ -n "$wp" ] && [ -f "$wp" ] && { echo "$wp"; exit 0; }

sess=$(find "$SP/dsh-home-linux/sessions" -path "*$label*" -name session.jsonl.zstd 2>/dev/null | head -1)
if [ -n "$sess" ]; then
  wp=$(zstd -dc "$sess" 2>/dev/null | grep -aoE '"path":"[^"]*\.html' | grep -v smoke | head -1 | sed 's/.*":"//')
  [ -n "$wp" ] && [ -f "$wp" ] && { echo "$wp"; exit 0; }
fi

exit 1
