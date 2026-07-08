#!/bin/sh
# Lens-study launcher — E1 (generalist model-compare) + E2 (ornith concern-lens fanout).
# Ollama lanes: per-instance sequential queues on the 3 card-pinned private instances.
# Hosted lanes (codex/openrouter): fully parallel, no box contention.
set -u
cd "$(dirname "$0")/.." || exit 1
R="python3 harness/review_runner.py"
ORN="ollama:ornith35b-q4-32k"
export OPENROUTER_API_KEY="$(tq -f "$HOME/.config/agent/config.toml" -r openrouter.api_key)"
mkdir -p results/lens-study
L=results/lens-study

# hosted lanes — parallel
( $R --models codex:gpt-5.5                        --keep-findings --label gen-gpt55 > $L/gen-gpt55.log 2>&1 ) &
( sleep 2; $R --models openrouter:z-ai/glm-5.2     --keep-findings --label gen-glm52 > $L/gen-glm52.log 2>&1 ) &
( sleep 4; $R --models openrouter:moonshotai/kimi-k2.7-code --keep-findings --label gen-kimi > $L/gen-kimi.log 2>&1 ) &

# instance queues — each card serial, cards parallel
( export OLLAMA_API_BASE=http://${LENS_BOX:-127.0.0.1}:11439
  sleep 6;  $R --models $ORN --keep-findings --label gen-ornith       > $L/gen-ornith.log 2>&1
  $R --models $ORN --keep-findings --system-file harness/lenses/correctness.txt --label lens-correctness > $L/lens-correctness.log 2>&1 ) &
( export OLLAMA_API_BASE=http://${LENS_BOX:-127.0.0.1}:11440
  sleep 8;  $R --models $ORN --keep-findings --system-file harness/lenses/security.txt    --label lens-security  > $L/lens-security.log 2>&1
  $R --models $ORN --keep-findings --system-file harness/lenses/contracts.txt   --label lens-contracts > $L/lens-contracts.log 2>&1 ) &
( export OLLAMA_API_BASE=http://${LENS_BOX:-127.0.0.1}:11441
  sleep 10; $R --models $ORN --keep-findings --system-file harness/lenses/concurrency.txt --label lens-concurrency > $L/lens-concurrency.log 2>&1
  $R --models $ORN --keep-findings --system-file harness/lenses/claims.txt      --label lens-claims    > $L/lens-claims.log 2>&1 ) &
wait
echo "LENS_STUDY_COMPLETE" > $L/COMPLETE
