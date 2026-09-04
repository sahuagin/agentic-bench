# Discovery without front-loading: what the evidence says, what to build in mu

Synthesis of the three researcher memos (01-03) and the mu-96ga9 battery, 2026-09-04.

## What the battery established

Twenty-one runs, two models, one small Rust task with failing tests, four arms (sentences + host `plan` CLI; plain; sentences with `plan` registered in t4c; production shape with MU.md + AGENTS.md injected and `discover` in the tool list). Zero `discover`, `t4c` or `plan` calls in any run. qwen3.8-27b passed 8 of 15 with the losses runtime-shaped (a guard-refusal loop, hung test binaries); GPT-5.5 passed 6 of 6 in one to three iterations and, told to "write down a plan", printed one with `printf` rather than look for a tool. The task never creates an unmet need: read, write, edit, bash and cargo are in hand from turn one, so "which tool" never arises for either model. This matches the public record: process prose gets near-zero compliance even from frontier models (memo 03 §4: 0% by default; AGENTS.md files show no success effect and >20% cost), and small models that internally know a tool is needed still do not act on a prompt (memo 02 §4).

## The mechanism that does not depend on propensity

Harness-side selection. mu runs its own ranker on the user prompt (and again on each new user message or on an unknown-tool error) and puts the top five to seven matching tool schemas, plus matching skill metadata, into the tool list. The model sees core tools, a `discover` stub and a small rotating relevant set; everything else is deferred, including procedure prose. This is the only mechanism with evidence on a Qwen base (RAG-MCP: 13.6% to 43.1% accuracy at half the prompt tokens) and on 7-8B models (Less-is-More), because the model never has to decide to search. mu already has the ranker: `discover` and `t4c_source` build and rank the same manifest (memo 01 §3). What is missing is running it at assembly and filtering `tool_specs` by a session-mutable loaded set (memo 01, attachment point 4). Retrieval quality is the ceiling (off-the-shelf nDCG@10 around 34 on ToolRet), so the stub stays for second chances.

## The trigger that fires at the moment of need

Names manifest plus hydrate-on-touch. Deferred tools appear as names (ten to twenty tokens each); a call to an unhydrated name is intercepted at the not-found branch (`execute_tools.rs:936-953`) and either auto-hydrated and re-executed, or refused with the top three `discover` matches. A failed call is the one signal weak models reliably react to, and Claude Code's own tracker shows the refusal alone is not enough (models skip the search because they know the name), so hydrate rather than just refuse. The known weakness is substitution through bash, which the pre-selection above mitigates.

## Rails, not prose

- **Refusal ladder with a floor** (mu-ucjhg). Repeats one and two: the block is the tool observation, stating count and "result unchanged". Repeat three: mask that tool for the next call. Repeat five, or a hard step budget: end the turn with a STUCK status and the partial work. mu has the in-band guard and not the out-of-band rail; every surveyed harness that survives loops has both.
- **Process-group kill and a structured exit marker** (mu-c1b3t). Kill the group on timeout; put exit code and a timeout marker in the result. No model behaviour involved.
- **End-of-turn verification gate.** If a source edit happened after the last green test run, refuse `final_answer` once with "tests have not run since your last edit", then let the second through. LangChain's equivalent checklist was the largest single lever in a +13.7 point Terminal Bench gain with the model fixed. Condition it on a known test command and a source edit so it cannot over-gate.
- **Plan as a schema, forced, only where it is load-bearing.** A `todo_write`-shaped tool with replace semantics and the one-in_progress invariant, forced as the first call via named `tool_choice` on tasks above a size threshold. The battery shows it buys nothing on a small task; build it after a task class shows the plain arm losing the thread. `tool_choice` on the vLLM/Qwen3 non-thinking stack has open guided-decoding bugs and must be terrain-checked first.

## Context diet

The production system message is 11.6 KB of MU.md and AGENTS.md, and the default tool block is another 9.4 KB (memo 01 §1-2). The procedure half of AGENTS.md has no measured effect and pays the context-rot cost (memo 02 §3: 11 of 13 models below half their short-context score at 32K; Qwen3 degrades monotonically). Keep facts, drop procedure, and let deferral cut the tool block. Keep the short bootstrap line and the ~100-token skill descriptions because the ranker searches over them, not because they steer.

## What to measure next

The plan-ab rig now takes role-resolved targets and a direct lane. Two tasks are missing: one with a genuine unmet need, where a capability is reachable only through a deferred or host tool (the web probe, or an agent_tools-only CLI), which is the probe for pre-selection and hydrate-on-touch; and one long enough that the plain arm loses the thread, which is the probe for a plan schema and a pinned plan span.

## Order of work

1. Rails: refusal ladder with a floor, process-group kill (S each; close the two observed losses).
2. Verification gate at end of turn (S).
3. Harness-side pre-selection and deferred schemas with hydrate-on-touch (M; one design, two halves).
4. Plan schema and pinned span, only after a discriminating task exists.
