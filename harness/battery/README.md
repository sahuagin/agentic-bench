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
