#!/usr/bin/env bash
# Serialized local-model bench on .143: ONE model loaded at a time, ollama stop
# between each (per the gpu-thrash incident — never interleave). Same cases +
# scorer as the hosted run, so results merge in summarize.py.
set -uo pipefail
cd "$(dirname "$0")/.."
HOST=127.0.0.1
OUT=results/local
mkdir -p "$OUT"

MODELS=(
  "gpt-oss:120b"
  "gpt-oss:20b"
  "gemma4:31b"
  "gemma4:31b-it-q8_0"
  "deepseek-r1:32b"
  "glm-4.7-flash:q8_0"
  "glm-4.7-flash:bf16"
  "qwen3.6:35b-a3b-q8_0"
  "qwen3.6:27b"
)

for m in "${MODELS[@]}"; do
  echo "===== $m :: VRAM before =====" >&2
  ssh "$HOST" 'nvidia-smi --query-gpu=memory.used --format=csv,noheader' 2>&1 | paste -sd' ' - >&2
  echo "===== $m :: running =====" >&2
  python3 scripts/run_benchmark.py --models "$m" --warmup \
    --num-ctx 16384 --num-predict 8192 --timeout 600 --out-dir "$OUT" 2>&1 | tail -2 >&2
  echo "===== $m :: ollama stop =====" >&2
  ssh "$HOST" "ollama stop '$m'" >/dev/null 2>&1
  sleep 3
done

echo "===== restore resident state (primary @262144 + embedding), keep_alive=24h =====" >&2
ssh "$HOST" 'curl -s http://localhost:11434/api/generate -d "{\"model\":\"qwen3.6:27b\",\"prompt\":\"ok\",\"stream\":false,\"keep_alive\":\"24h\",\"options\":{\"num_ctx\":262144}}" >/dev/null; curl -s http://localhost:11434/api/embed -d "{\"model\":\"qwen3-embedding:8b\",\"input\":\"ok\",\"keep_alive\":\"24h\"}" >/dev/null; echo "--- ps after restore ---"; ollama ps' 2>&1 >&2
echo "DONE. local results in $OUT/" >&2
