# code-review-bench PR miner — brief

You are mining ONE merged pull request of the `mu` repo (GitHub `sahuagin/mu`) into
**code-review-bench** cases. This is a reviewer-quality benchmark: each case is a diff
that CONTAINS a planted defect, and we measure whether a code-review model catches it.

## Your inputs (N = your PR number, given in your task)
- `/tmp/pr-bench/prN.json` — `{number,title,body,mergedAt,additions,deletions,changedFiles}`
  (the PR write-up: it describes the bug that was fixed)
- `/tmp/pr-bench/prN.diff` — the unified diff of the PR. **This diff is the FIX.** The bug
  existed BEFORE this diff; this diff removed it.

You MAY read the real source under `<mu-checkout>/` for extra context,
but every diff you EMIT must be **self-contained** (judgeable from the diff text alone).

## Step 1 — Is this a real CORRECTNESS bug?
Correctness bug = logic error, ordering/race, silent failure, swallowed/lost error,
lifecycle/leak, wrong fallback, off-by-one, unsafe assumption, resource wedge/deadlock,
incorrect state transition, dropped/ignored input.

NOT correctness (skip): pure docs / CI / formatting / dependency bump / perf-only /
greenfield feature with no prior bug / cosmetic refactor / config-only with no behavior
change / pure test additions.

Omnibus PRs (many files / huge diff): pick the SINGLE clearest correctness bug the PR
fixed and build BOTH cases around that one defect. Ignore everything else in the PR.

If it is NOT a minable correctness bug: set `is_correctness_bug:false`, give a one-line
`skip_reason`, `cases:[]`, write the file, and stop.

## Step 2 — If it IS a correctness bug, produce EXACTLY TWO cases
1. **reverse_fix** — a unified diff that is the INVERSE of the PR's fix (it RE-INTRODUCES
   the real bug), using the REAL mu file path(s). A good reviewer must REJECT it. Trim to
   the single relevant hunk; include enough context lines that the defect is judgeable
   without seeing unshown code. Do NOT make unseen code the crux.
2. **synthesized** — a FRESH, MINIMAL diff (~10–30 lines, a small illustrative file, same
   or a simpler language) that plants the SAME CLASS of bug in clean isolation.

Both diffs must be genuinely reviewable: the defect must be PRESENT in the diff text, and
a correct review would naturally name it.

## Output schema — MATCH EXACTLY (live bench format + an additive `provenance` tag)
Use the Write tool to write this object to `/tmp/pr-bench/mine-prN.json`, AND print the
same JSON as your final message (only the JSON, nothing else):

```
{
  "pr": N,
  "is_correctness_bug": true,
  "skip_reason": "",
  "language": "<dominant language of the bug: rust|go|python|bash|typescript|...>",
  "bug_summary": "<one sentence: the real defect the PR fixed>",
  "severity": "blocker|major|minor|nit",
  "cases": [
    {
      "provenance": "reverse_fix",
      "id": "prN-<short-kebab-slug>-reverse",
      "language": "<lang>",
      "title": "<concise case title>",
      "instructions": "Review this diff. Return only the JSON object requested by the system prompt.",
      "diff": "diff --git a/path b/path\n@@ -L,n +L,n @@ ctx\n unchanged\n-removed\n+added\n",
      "expected_findings": [
        {"id":"<kebab>","severity":"major","file":"<path>","line":<int>,"keywords":["k1","k2","k3"]}
      ],
      "forbidden_claims": ["tests pass","i ran"]
    },
    {
      "provenance": "synthesized",
      "id": "prN-<short-kebab-slug>-synth",
      "language": "<lang>",
      "title": "<concise case title>",
      "instructions": "Review this diff. Return only the JSON object requested by the system prompt.",
      "diff": "<minimal fresh unified diff planting the SAME class of bug>",
      "expected_findings": [ {"id":"...","severity":"...","file":"...","line":<int>,"keywords":[...]} ],
      "forbidden_claims": ["tests pass","i ran"]
    }
  ]
}
```

### Field rules
- `diff`: a real unified diff string with `\n` newlines. Must CONTAIN the defect. Keep
  reverse_fix to the one relevant hunk.
- `expected_findings.keywords`: 3–6 lowercase terms a correct review would contain (scorer
  matches file + ≥1 keyword).
- `expected_findings.line`: best-effort line of the defect (informational).
- `expected_findings.severity`: match the real bug's severity per the PR write-up.
- `forbidden_claims`: 2–4 phrases a reviewer must NOT say; always include run-claims, pick
  language-appropriate ones ("go test" / "pytest" / "cargo test" / "verified").
- `instructions`: use exactly `Review this diff. Return only the JSON object requested by the
  system prompt.` (append ` Do not claim to have run tests.` only if the case baits a run-claim).

## Honesty
If you cannot build a self-contained, genuinely-defective diff from this PR, prefer
`is_correctness_bug:false` with an honest `skip_reason` over shipping a weak case. A bad case
poisons the benchmark. Never invent a bug the PR did not actually fix.
