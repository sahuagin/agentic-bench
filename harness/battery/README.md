# harness/battery — the harness-experiment batteries (mu-316wl phase 2)

Drivers, scorers, wire-capture proxies and the real-browser grader for the
"can a harness add capability past a model's threshold" experiment
(mu beads mu-nd9p2 phase 1, mu-316wl phase 2; writeups in mu
`research/harness-internals/`). Moved here from session scratchpads on
2026-09-03 so they outlive the session that wrote them. Results, captures and
run workspaces are NOT committed: point `BATTERY_DIR` at a scratch directory.

## The real-browser grader: `../web_probe.py`

Runs an HTML artifact in a real headless Chrome and reports what the runtime
says: uncaught exceptions with locations, console output, requestAnimationFrame
ticks/s, animation (two screenshots a second apart differ), input response
(pixels change after a scripted click + key burst), exceptions during input,
screenshot paths. First line is `VERIFY web PASS|FAIL`; exit code follows it.
This is the T3 grade and the verifier the battery-1 finding is about: a Node
DOM stub cannot see `null.getContext` / wrong-type / undefined-function bugs;
a real browser throws them on load.

Chrome lives on the GPU box (CPU-only WebGL via SwiftShader, no contention
with the inference serves), reached over a CDP tunnel:

```sh
harness/battery/rig-up.sh          # chrome-for-testing on the box :9222, tunnel jail :9223
PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright /usr/local/bin/python3.11 \
  harness/web_probe.py game.html --remote-host ollama --json out.json
harness/battery/rig-down.sh
```

Install on the box (once, user-level, no root): Google's chrome-for-testing
tarball at `~/chrome-test/chrome-linux64/chrome`; all shared libs were already
present on debian13. Flags that matter: `--headless=new --disable-gpu
--enable-unsafe-swiftshader --no-sandbox` (`--use-angle=swiftshader` alone is
deprecated and fails). Headless Firefox cannot do WebGL anywhere. The jail's
playwright is the py3.11 one with `PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright`.
`--chrome /path` launches a local Chrome instead of connecting.

Gotchas: WebGL canvases without `preserveDrawingBuffer` read back empty
in-page, so render checks hash `page.screenshot()`; SwiftShader boot is slow
(8 s default settle; a Three.js voxel game reaches rAF ~6-20/s); favicon 404s
are filtered.

## Batteries

| script | measures |
|---|---|
| `battery1-driver.sh` | output past one completion budget (minecraft mega-prompt), mu vs DeepSeek Harness vs Claude Code, thinking on/off; T0/T1 inline, T3 via `grade.sh` |
| `battery1-rerun.sh` | same task, mu only, n=3 — the acceptance shape for a mu lever (was the `verify` tool, mu PR #591, closed) |
| `battery2-driver.sh` + `battery2-score.py` | context larger than comfortable (compaction × envelope) |
| `battery3-driver.sh` + `battery3-score.py` + `recall-probes/` | knowledge not in the repo: memory_recall tool vs bare, four planted facts, paraphrased probes |
| `battery4-driver.sh` | tool confinement (the run that caught the mesh confused-deputy, mu-3si78) |
| `memory-hints-acceptance.sh` + `memory-hints-analyze.py` | mu `[recall].memory_hints` (PR #588): the probes with injection on and no recall tool, requests read off the wire |
| `grade.sh <label> <html>` | T0 complete-file / T1 `node --check` / T3 web_probe, one line |
| `vllm-proxy.py`, `parse-wire.py` | capture proxy for the vllm lane and its offline parser (see the redaction banner in parse-wire before extracting anything) |
| `resolve-artifact.sh` | find -x where a run's model actually wrote its file (they scatter) |
| `plan-ab/` (`driver.sh`, `score.py`, `plan`, `sys-A.txt`, `prompt.txt`, `task/durparse`) | mu-96ga9: does dsh's plan + act-on-failures mechanism move mu's pass rate with NO mu change? A = discovery bootstrap + plan/exit-code sentences + host `plan` CLI; B = plain non-bare; Rust crate task verified by `cargo test` run by the driver |

`parse-wire.py` scrubs request bodies by default; `NO_SCRUB=1` keeps message text
for the local scorers (`memory-hints-analyze.py`, `plan-ab/score.py`) — that output
must never be committed.

Lane: `qwen3.8-27b-nvfp4` on the GPU box's vLLM at :11435, through the proxy on
127.0.0.1:8435 so every request is captured. The drivers need `mu` on PATH (or
`MU=/path/to/mu`) and a `[[providers.endpoints]]` entry named `vllm143` pointing
at the proxy; an isolated `XDG_CONFIG_HOME` per battery keeps the operator
config untouched.

## Findings that shaped this (short)

- Self-verification against the real runtime is the lever (battery 1); DeepSeek
  Harness reached it via `todo_write` planning + "check every exit code" in its
  prompt, plus a completed write phase — two of its three wins used a Node stub
  iterated 80-160 requests, one used a real browser.
- mu's losses were upstream of verification: the write phase (stall watchdog
  mu-b82rr, fixed; truncated tool-call JSON loop mu-gg2yf, open).
- Text-only PASS can sit on a visibly broken render (stretched terrain): image
  feedback is its own track (mu-92vvk).

## plan-ab (mu-96ga9) — result, 2026-09-03

Question: does dsh's mechanism (turn-one plan with a test step kept current +
"check every exit code, investigate failures") move mu's pass rate on our model,
with NO mu change? Arm A = daemon discovery bootstrap + those sentences in the
system prompt + a host `plan` CLI on PATH; arm B = plain non-bare `mu ask`.
Task: `task/durparse`, a dependency-free Rust crate with a feature to add
(compound terms, `d`/`ms`, a new `MissingUnit` variant) and 14 failing tests;
verifier = `cargo test` run by the driver after the model exits, tests/ diffed
against the template. qwen3.8-27b-nvfp4, thinking off, unlimited turns, 1200 s
wall cap, n=3 per arm, interleaved.

| run | wall | requests | cargo test runs | failing results | edits after 1st failure | `plan set` | final_answer | cargo test |
|---|---|---|---|---|---|---|---|---|
| A-1 | 1200 s (timeout) | 250 | 9 | 10 | 8 | 0 | no | FAIL |
| A-2 | 69 s | 12 | 4 | 2 | 4 | 0 | yes | PASS |
| A-3 | 62 s | 15 | 5 | 3 | 6 | 0 | yes | PASS |
| B-1 | 80 s | 18 | 7 | 6 | 7 | – | yes | PASS |
| B-2 | 239 s | 54 | 25 | 14 | 20 | – | yes | PASS |
| B-3 | 55 s | 12 | 5 | 3 | 3 | – | yes | PASS |

- A 2/3, B 3/3: the sentences did not raise the pass rate. Do not build the
  native plan tool / pinned plan span on this evidence.
- The plan sentence never bound: zero `plan set` calls in three A runs and the
  word "plan" never appears in assistant text. A sentence without a tool schema
  does not produce planning in this model; testing schema-level salience needs
  the tool in the tool list (MCP-served or native), which this battery did not do.
- The exit-code sentence had nothing to bind to: all 46 `cargo test` calls across
  the six runs were `cargo test 2>&1 | tail -N`, so bash's status was tail's (0)
  and mu's `exit: <code>` marker never appeared on a test failure. The model
  acted on the `test result: FAILED` / `could not compile` text instead, in every
  run of both arms: plain non-bare mu already runs the test/fix loop when the task
  names its tests.
- The one failure is a runaway refusal loop, not a verification miss: after its
  fix did not work, A-1 built a standalone probe program under /tmp and resubmitted
  the identical bash call 213 times. mu's loop guard refused 5 times and the retry
  guard 200 times, each refusal a fresh round trip with empty assistant text,
  until the wall cap. The guards annotate and refuse but nothing ends the turn
  (filed as a mu bead).
- B-2's 239 s was a long compile-error series (13 failing results over 25 test
  runs) that converged; every other run converged in 4-7 test runs.

Rerun: `BATTERY_DIR=<scratch with testcfg/> harness/battery/plan-ab/driver.sh`
(`REPS`, `ARMS`, `RUN_TIMEOUT` env overrides; results in `$BATTERY_DIR/results.log`).

### Arms T and P

Addendum (same day, arms T and P). The first six runs had a design hole: `[recall].enabled = false` plus an isolated config dir meant MU.md and AGENTS.md (the discover-first directive) were in none of them, and `plan` was never registered with t4c, so the discovery path was never given the tool. Arm T: the same sentences with no tool named; `plan` registered with t4c through an override catalog (`$T4C_CONFIG`, snapshot fresh, `t4c find` ranks `bash.plan` first). Arm P: production-shaped: recall on, the operator's MU.md + AGENTS.md injected as the system message (verified on the wire: 11.6 KB, no bootstrap, `discover` in the tool list), memory injection off, no prompt file, `plan` registered with t4c. Runs after T-1 were capped at 240 s: whether the model looks for a plan tool is settled in its first few calls.

| run | wall | requests | cargo test runs | plan / t4c / discover calls | result |
|---|---|---|---|---|---|
| T-1 | 1140 s (stopped) | 44 | 6 | 0 | unfinished |
| T-2 | 80 s | 16 | 7 | 0 | PASS |
| T-3 | 240 s cap | 17 | 6 | 0 | FAIL, hung test (infinite loop in the model's parser) |
| P-1 | 74 s | 13 | 6 | 0 | PASS |
| P-2 | 244 s cap | 11 | 3 | 0 | FAIL, hung test |
| P-3 | 241 s cap | 95 | 81 | 0 | FAIL, guard-refusal loop (76 identical `cargo test` calls) |

Across all fifteen runs, zero calls to `discover`, `t4c` or `plan`, including the three with the production system context whose AGENTS.md says to call `discover` on first substantive use. The task never produces an unmet need: read, write, edit, bash and cargo are in hand from turn one, so "which tool" never arises, and a directive to plan does not make this model look for a plan tool. Pass rates under the 240 s cap are not comparable with the 1200 s runs. Two more mu-side findings: test binaries outlive the bash tool's timeout (mu-c1b3t), and the guard-refusal runaway reproduced in P-3 inside four minutes (mu-ucjhg).
