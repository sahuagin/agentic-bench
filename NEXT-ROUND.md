# agentic-bench — Next Round (design + live status)

Resumable plan for the next round of model benchmarking. **If you're picking this
up after a lockup or handoff, read STATUS at the bottom first**, then this.

## Why this round
The current benches saturate. Coding score is a 3-bucket verdict (pass /
compiles-but-fails / no-compile) and self-contained fix cases let every capable
model hit the top bucket. We need **harder, more representative cases + finer
grading**, plus the untested **division-of-labor review**.

## Threads, in priority order

### 1. Multi-focus review panel  — FIRST (quick; reuses the corpus + idle compute)
N reviewers, each with ONE focus, spread cross-provider for independent blind
spots:
- code invariants honored
- architecture guideline / conformance
- idiomatic for the language
- tests appropriate + coverage
- request vs delivered (omissions; does it actually solve the problem)
- edge cases considered

Run on the existing **17 real-PR (reverse_fix) cases** in
`cases/code-review/cases-final.json`. Compare the lens-panel UNION of findings
against a single GENERIC reviewer: does division-of-labor raise catch-rate,
especially on the whole-artifact misses (symmetric-wrong contracts, omissions /
complement-of-the-diff, emergent invariants) that a diff-by-diff reviewer
structurally cannot see?

Harness: `harness/lens_panel_runner.py` (reuses `review_runner` scorer + corpus).
Each lens = a focused SYSTEM prompt; assign lenses across models (gpt-5.5 + the
proven locals). Baseline = a generic single reviewer on the same cases.

### 2. Redo-bead coding cases — calm pass (mining + test curation)
Real "landed then redone" tasks are the representative, saturation-breaking
corpus. Candidate areas (operator-named): `mu-anthropic`, `mu-openai`,
`mu-analytics` (~5 redos), `agent_tools` do-overs, `mu-dialogue`. Reconstruct the
lineage from the beads tracker + git history (original commit -> revert/rework ->
final landed). Each yields a 4-tuple: **spec · bad first attempt · the redo ·
the tests that eventually landed.**

Two case types:
- **(a) first-attempt quality** — feed the original spec; does the agent write
  the *kind of code that got sent back*?
- **(b) redo task** — feed the bad code + the rejection rationale; can it produce
  the rewrite (or an approximation)?

Grade with main's **real landed tests**, not synthetic injected ones. The
judgment-heavy part: curate to tests that pin BEHAVIOR, not ones welded to the
incidental new API, so an approximate-but-correct rewrite still passes.

### 3. Finer scoring (kills the saturation)
Beyond pass/fail, grade dimensions that separate models: diff-churn vs the
reference fix, conciseness (the gpt-151-vs-locals-295 signal), invariant
conformance, test-coverage delta, # of review rounds to converge.

### 4. Concurrency — use the box headroom
A single 35B request uses ~½ the box VRAM and 30–50% compute (bursts ~80%) —
serial dispatch leaves the box idle. `OLLAMA_NUM_PARALLEL=2`. Levers: raise
parallelism + dispatch concurrent requests. This is the compute budget for the
chunked map-reduce (one model resident, N parallel sub-requests on chunks of a
decomposed task). Bench single-shot vs chunked-parallel; it also speeds the bench.

## Operating lessons — apply to EVERY run (hard-won this session)
- **PRE-WARM** each ollama model and confirm it responds before timing work.
  Empty / timeout output = the model never LOADED (dispatch failure), NOT
  incapability. Never score silence as a result. (`coding_runner.py` does this.)
- **Build off the volatile temp dir.** Reuse the warm copied target
  (`~/cbench/target-shared`); a fresh `CARGO_TARGET_DIR` forces a from-scratch
  `ring` rebuild that collides with mu's `.cargo/config.toml` relative-TMPDIR
  convention. Reuse the warm target so only mu-core recompiles.
- **jj workspaces** per model (not `git worktree`); `jj workspace forget` AND
  `rm -rf` the dir (forget only drops the repo's pointer).
- **Toolchain**: run cargo with cwd = the workspace so `rust-toolchain.toml` pins
  stable (correct). PATH uses the rustup shims.
- **VCS**: branch + PR, NEVER push to main directly. `bot-jj` is github-shaped
  (origin preflight, fails on forge); `jj-hp` is the forge path; both push
  bookmarks. Forge PRs are the Forgejo web (no `bot-gh` for forge).

## Autonomous run contract (overnight)
GOAL: drive the priority queue above (lens panel → add the reduce step →
redo-bead mining/staging) without pausing for prompts. Process each result,
launch the next step, commit + update STATUS incrementally (lockup-safe), apply
every operating lesson.

KEEP GOING — do NOT end a turn with "continue or pause?": between phases, after
each run, on recoverable failures (retry / route around / drop a bad case).

SURFACE TO OPERATOR (and only then) when:
- a result needs operator JUDGMENT to proceed — curation calls (which redo-beads
  / which landed tests to finalize), or whether a finding warrants a router/config
  change;
- an action needs sign-off — push to main, anything destructive/external, creds,
  spend, genuinely ambiguous scope;
- blocked — ollama lease lost to another owner, ≥2 consecutive unrecoverable run
  failures, or the in-scope queue is exhausted;
- the overnight goal is met — lens panel + reduce concluded AND redo-bead
  candidates staged for review;
- operator says stop.

MECHANISM: background-run completions re-invoke automatically → process + launch
the next; keep one step in flight so the chain never idles; a long fallback
wakeup guards a hung run that never notifies.

## STATUS  (newest first — update as you go)
- HARDCODE CASE *CARGO-VERIFIED* (isolated + repeated, via `cargo_verify_diff.py`:
  apply diff to a jj workspace at base, warm target-shared, cargo check + test —
  corrects the lens grade):
    WORKS (compiles + mu-coding tests pass): gpt-5.5 · deepseek · ornith (LOCAL, 357) ·
      opus (sweep "6 failed" was a FLAKE — 3/3 isolated pass). sonnet: unit tests
      pass, only a rustdoc DOCTEST fails (EtcdSource line 302).
    REAL FAIL: qwen3.6:35b-a3b (E0593 closure-signature, deterministic, 3/3).
    No compile (incomplete): gpt-oss (partial) · qwen3.6-code (hardcoded).
  => 5/6 data_driven attempts actually work; the LOCAL (ornith) is in the working set.
  RETRACTED my earlier "frontier ships broken code" — flake artifact. LESSON: a
  shared CARGO_TARGET_DIR sweep + tests that hit LIVE etcd flake → verify ISOLATED +
  REPEATED, never trust a one-shot sweep failure.
  BOX FACTS: 3x RTX PRO 4000 Blackwell 24GB (=72GB), host debian13rtx4000; ollama on
  :11434 (mu config's :11435 is a proxy); ornith:35b=21GB fits one card (weights),
  262144-ctx KV spills to a 2nd. ollama-box agent-slot lock held by ANOTHER cc on
  threadripper (wrongly; operator says pre-emptable). 1-card throughput probe running
  (`bev0hifyu`, num_ctx=8192).
- HARDCODE COMPLETE across 9 models / 4 channels; all code in
  `results/generated/hardcode-FINAL/`. data_driven 1.0: gpt-5.5(419) · opus(996) ·
  sonnet(1299) · deepseek(369, openrouter — slow, timed-out-but-produced) ·
  qwen3.6:35b-a3b(389, LOCAL, needed 2x budget). partial: gpt-oss(106). hardcoded:
  ornith(14). empty: qwen3.6-code + glm-5.2 (over-investigate → no edit budget).
  DIAGNOSIS (mu event logs, ornith): 21 lookups (11 read+9 grep) + ~496K cumulative
  ctx + 42K reasoning-heavy output → stop_reason=max_tokens, edit truncated. So the
  limiter is OUTPUT BUDGET vs investigate/reason style — not load/dispatch.
  LEVER TEST running (`b0npp5t09`): ornith + qwen3.6-code at `--thinking low` — does
  less reasoning free budget to finish the edit? NEXT design: decompose pipeline
  (strong model = architect/decomposer → scoped sub-tasks → locals execute bounded
  pieces; generalizes via the decomposer, validated by this diagnosis).
- OPENROUTER UNBLOCKED (operator gave the key path; loaded via `tq -f
  ~/.config/agent/config.toml -r openrouter.api_key`, never streamed to context).
  Running the hardcode case on `deepseek/deepseek-v4-pro` + `z-ai/glm-5.2`
  (`bofii0q04`, cloud — no box). Will lens-grade (gpt-5.5) + add to
  `results/generated/hardcode-FINAL`.
- === MORNING SUMMARY (loop stopped ~04:12, box released) ===
  THREAD 1 (lens panel + reduce): DONE. Division-of-labor review works; gpt-oss:20b
  is the best reduce-adjudicator (held recall 1.0, cut FP 134->32, beat gpt-5.5 which
  over-pruned). Single-bug corpus ceilings recall — the panel's real edge needs the
  redo corpus.
  THREAD 2 (hardcoding case mu-8puo.1, invariant-lens graded): DONE across 7 models.
  ALL generated code saved + annotated -> `results/generated/hardcode-FINAL/` (6
  diffs + MANIFEST) for your review.
    data_driven 1.0: gpt-5.5 (419ln, tightest) · opus (996) · sonnet (1299) ·
      qwen3.6:35b-a3b-q8_0 (389 — needed 2x budget=22min; SLOW BUT CAPABLE, a real
      local win)
    partial 0.5: gpt-oss:20b (106, lean/fast/shallow)
    hardcoded 0.0: ornith:35b (14ln — kept the const = the pain, manifest)
    empty: qwen3.6-code (couldn't drive the agentic edit even at 2x budget)
  Diagnosed: the local empties were SLOW agentic execution (over-investigate, don't
  reach editing in budget), NOT load/dispatch failures -> validates your chunk/
  decompose idea (small pieces per local).
  NUANCE for you: the spec DEMANDS data-driven, so this measured EXECUTION, not
  default-hardcoding TENDENCY. A spec that does NOT mention data-driven would test
  "do they hardcode by default" — likely the more revealing version of your pain.
  PARKED on you: openrouter GLM-5.2 + deepseek-v4-pro (need OPENROUTER_API_KEY or
  "use agent-dispatch"). NEXT cases staged: mu-anthropic usage-bug (case b, cargo);
  the chunked map-reduce coding test; redo lineages (dialogue/providers/event/cap).
- DIAGNOSIS (ornith empty): NOT load-fail, NOT dispatch bug. The thinking-heavy
  locals OVER-INVESTIGATE (glob/grep/read + long planning) and are too SLOW to
  reach the EDIT step within budget -> empty (timeout/early-exit). Captured output
  shows ornith still *designing/reading* at 500s, 0 edits. gpt-oss:20b (lean, less
  thinking) edited (partial). So the edit tool works; the limit is agentic
  EXECUTION SPEED on a big task — which VALIDATES the chunk/decompose idea (small
  pieces per local). Re-running the 3 empties at 1800s to confirm slow-vs-incapable
  and capture their code if they finish.
- HARDCODE case DONE + COLLECTED -> `results/generated/hardcode-combined-f90d6525393/`
  (4 annotated diffs + MANIFEST for operator review). Result: cloud
  (opus/sonnet/gpt-5.5) data_driven 1.0 (gpt-5.5 tightest at 419ln vs 996/1299);
  gpt-oss:20b partial 0.5 (ONLY local to produce, 106ln); qwen3.6-code + ornith
  empty (rc1 ERROR); qwen3.6:35b-a3b empty (timeout). NUANCE for review: the spec
  explicitly DEMANDS data-driven, so this measures EXECUTION (can they do the
  refactor) more than default-hardcoding TENDENCY — and every producer went
  data-driven (none hardcoded). A "does it hardcode by default?" case needs a spec
  that does NOT mention data-driven. Diagnosing ornith rc1 (`bqiziujbh`): edit-tool
  failure (fixable) vs tool-use capability limit. Then: openrouter (on key), next case.
- HARDCODE results landing: data_driven 1.0 = opus (996ln), sonnet (1299ln),
  gpt-5.5 (419ln) — the CLOUD models did the data-driven refactor. LOCALS
  qwen3.6-code + ornith = empty_diff, rc1, ~113-260s — PRE-WARM PASSED, so NOT a
  load fail: the `mu ask --tools edit` worker ERRORED for them. DIAGNOSE before
  any capability claim: re-run one local with worker stdout+stderr captured (the
  harness discarded it) to find the rc1 cause (likely the edit-tool path on a big
  agentic task). qwen3.6:35b-a3b + gpt-oss pending. Cloud diffs saved as
  `~/cbench/hc_*.diff`; run `collect_generated.py` over box+subagent jsonls when
  the box run lands.
- BROADENED (operator 2026-06-30): hardcoding case now across gpt-5.5 + 4 locals
  (box run `bs2tkgjq2`) + opus + sonnet (cc subagents `a079b039`/`a1eea54d`, each
  self-creates a jj workspace + edits). openrouter GLM-5.2 + deepseek-v4-pro
  PARKED on `OPENROUTER_API_KEY` (not set; won't scan for it — set it or route via
  agent-dispatch). ALL generated diffs -> `results/generated/<run>/<model>.diff`
  (annotated header + MANIFEST.md) via `collect_generated.py` for operator review.
  Grader = invariant lens, fixed gpt-5.5. (Subagent diffs: capture `jj diff` in
  their workspace on completion, lens-grade, add to the run.)
- THREAD 2 BUILD: first case = HARDCODING (mu-8puo.1), invariant-LENS graded
  (operator chose lens over cargo, 2026-06-30). RUNNING `hardcode_case_runner.py`
  (`hardcode-*` results): gpt-5.5 + 4 locals each refactor action_recall's
  `DANGER_VERBS` data-driven from mu HEAD; fixed grader gpt-5.5 rules
  data_driven / partial / hardcoded on each diff. Q = which models hardcode?
  Next case: mu-anthropic usage-field bug (case b, cargo via the landed wire test).
- THREAD 2 (redo-beads): candidates FOUND via beads + memory + the current-state
  roadmap (the keyword git-grep was the wrong tool — redos hide as feat/refactor,
  but the beads tracker + roadmap name them). Concrete redo lineages:
    * dialogue receive: `mu-vf0z` (poll+cursor) -> `mu-rkhj` / git `6f476383`
      ("rip the client-side inbound poller", event-driven push) — COMPLETED, both
      halves landed. Cleanest case (a+b).
    * provider wire protocols ("both protocols"): `mu-anthropic` + `mu-openai`
      rewritten to clean wire crates (`anthropic-rewrite-casestudy.md`; original
      took 24 fix commits then full rewrite).
    * event system / analytics: `mu-cc-event-unification-lkma` epic (.6/.7/.8 —
      port legacy analytics onto the unified event substrate, remove legacy).
    * hardcoding (recurring pain): `mu-8puo.1` (DANGER_VERBS -> data-driven),
      `mu-9vi0` (turn-budget 15 -> config). bad=hardcoded-in-main; test=data-driven.
    * capability protocol: `mu-3nzm` (permission modes); mu-invariant-violations Inv2.
  BUILDING cases — start: dialogue (cleanest completed lineage) + hardcoding (the
  pain). Re-acquire the ollama lease before RUNNING models on them.
- REDUCE done (`lensreduce-*`) — THREAD 1 CONCLUDED. gpt-oss:20b reducer WINS:
  recall HELD 1.00, fp 134→32, score 0.795. gpt-5.5 reducer over-pruned (recall
  0.82, fp 0, 0.757). Raw union 1.00/134/0.723. So lens-panel+reduce works and a
  CHEAP LOCAL (gpt-oss:20b) is the best precision-filter (holds recall, cuts FP
  76%). CAVEAT: single-bug reverse_fix cases ceiling-out on recall — vs a single
  gpt-5.5 generic (0.88/fp0) the panel trades fp(32) for recall(1.0); the panel's
  real edge needs the redo-bead corpus (whole-artifact misses).
- LENS PANEL done (`lenspanel-20260630-014537`): UNION recall=1.00, fp=134,
  score=0.723 over 17 real-PR cases. Max recall (single-bug cases ceiling-out),
  but ~8 FP/case — six lenses each cry wolf; raw union is unusable on precision.
  parse solid (5/102 fail). → running the REDUCE step
  (`harness/lens_reduce_runner.py`: gpt-5.5 + gpt-oss:20b each adjudicate the
  union per case; question = does recall hold while FP collapses?).
- INIT: roadmap created. Next: build `harness/lens_panel_runner.py` and run the
  lens panel vs a generic baseline on the 17 real-PR cases (thread 1).
