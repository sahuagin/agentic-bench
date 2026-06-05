# agentic-bench

Tool-loop benchmark for locally-served LLMs, driven through a real agent
stack rather than a chat-completions harness.

Most local-model benchmarks measure single-shot generation. This one
measures the thing agentic work actually depends on: can the model form
a valid tool call, consume the result faithfully, hold state across
turns, and terminate with a grounded answer? Every case is **tool-gated**
— the answer is unreachable without executing the read/grep loop against
a pinned fixture repository.

## How it works

Each run invokes [`mu`](https://github.com/sahuagin/mu) (`mu ask
--provider ollama --tools read,grep`) from the fixture repo's directory
— the production agentic path, including whatever rescue/recovery layers
the stack carries — and grades the final answer by regex.

Case shapes (see `cases.json`):

| Shape | Catches |
|---|---|
| single-hop lookup | basic tool-call formation |
| symbol hunt | grep→read chaining |
| multi-hop | state across 3+ turns |
| faithful aggregation | using tool output vs priors |
| negative probe | fabricating behavior for a nonexistent symbol |

Scored per rep: correctness, dialect-leak detection (raw tool-call
markup escaping as text), and a distinct **fabricated** flag when the
negative probe answers with substance instead of "doesn't exist."

## Running

```sh
./run_benchmark.py                              # default model set, 3 reps
./run_benchmark.py --models qwen3.6:27b --reps 3
```

Models run as groups with full ollama eviction between them — resident
models from a previous group otherwise pollute the timings.

## Provenance

Born 2026-06-05 from the discovery that a model's tool calls can flake
into its training-native text dialect ~50% of the time through an
ollama-served agent loop, invisibly to single-shot benchmarks (fixed by
a rescue layer in mu PR #179). First results: a 27B dense model tied a
38.7GB MoE at 0.933 while the local code-review champion scored 0.667 —
review quality and agentic competence are different muscles.

Sibling of code-review-bench (single-shot review quality; not yet
published). `results/` is gitignored; runs are local artifacts.
