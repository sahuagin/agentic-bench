# Coding-worker bench harness (mu-9yy1)

End-to-end: hand a model a bead-style spec + a real repo at a base commit; it edits a clean
worktree (read/edit/grep/glob, no bash); score its diff against a HIDDEN acceptance test the
model never saw (apply diff onto base+test -> `cargo test`). 1 / 0.5 (compiles, wrong) / 0.

## Lanes (resolved via `agent-role`, not hardcoded)
- gpt-5.5  -> `mu ask --provider openai-codex` (mu-openai sub)
- sonnet/opus -> harness **Agent subagent** (NOT `claude -p` — hangs nested)
- ollama  -> `mu ask --provider ollama --model qwen3.6:35b-a3b-q8_0`
- openrouter -> `mu ask --provider openrouter --model deepseek/deepseek-v4-pro` (needs OPENROUTER_API_KEY exported)

## Case format
base commit (parent of a behavioral fix), crate, the hidden acceptance test + which test module
to inject it into, and a spec that does NOT leak the diff. The test must compile at base, FAIL at
base, PASS with the real fix. `score.sh <model-diff> <slug>` does reset->apply->inject->cargo test.

## Case-1: `effort-fallback` (from fix 8f6031f, mu-core route_catalog)
provider_effort_fallback / provider_default_effort had no `openai_api` arm. Hidden test:
`effort_config("openai_api", default)` must return `[low,medium,high,xhigh]` + default `medium`.

### Result 2026-06-24 — ALL FOUR PASS (1.0)
gpt-5.5, sonnet, deepseek-v4-pro, qwen3.6:35b-a3b-q8_0 each one-shot the minimal fix.
**Case-1 is EASY and NON-DISCRIMINATING** — and the spec leaked the answer values ("mirror
openai_codex: low/med/high/xhigh, default medium"). It validates the harness; it does not
separate models. Next: HARD, less-leaky cases (multi-file, subtler logic, under-specified) —
mined from mu / agent_tools / mu-analytics — which is where the worker seat actually fails.
