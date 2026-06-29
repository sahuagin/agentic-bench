#!/usr/bin/env python3
"""Adjudicate code-review-bench false-positives, then re-score + emit a
failure-case report. Operates on existing result JSONLs — no model re-runs.

The base scorer counts ANY finding beyond the seeded `expected_finding` as a
false positive and docks precision, with no way to tell a real extra bug from a
hallucination. This pass judges each distinct extra finding (a hosted model, so
it never contends with the local-model bench on the GPU) and:
  - re-scores precision crediting the genuine extras (not penalizing them),
  - reports per-case MISS RATE (how many models missed the seeded bug -> the
    hard-case / canary list) and the REAL extra findings (on reverse_fix cases
    = actual mu code, these are candidate bugs we never seeded).

Usage:
  python3 adjudicate.py <run.jsonl> [<run2.jsonl> ...]
      [--judge-model gpt-5.5] [--timeout 120] [--limit-fp N] [--out PATH]
Re-runnable: same inputs -> same adjudication; diff the failure-case report over
time as a degradation canary.
"""
import sys, os, json, argparse, subprocess, tempfile, signal
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_benchmark as rb
from collections import defaultdict

MU = os.environ.get("MU_BIN", os.path.expanduser("~/src/public_github/mu/target/release/mu"))
JUDGE_SYS = ('You adjudicate code reviews. Given a diff and an issue a reviewer flagged that is '
             'NOT in the seeded answer key, decide if it is a GENUINE defect actually present in '
             'the diff (true) or a hallucination / misread / non-defect style nit (false). '
             'Reply ONLY compact JSON: {"real": true|false, "why": "<=12 words"}.')


def load(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def fblob(f):
    return rb.norm(" ".join(str(f.get(k, "")) for k in ("file", "summary", "rationale", "id")))


def judge(model, diff, finding, timeout):
    payload = {k: finding.get(k) for k in ("file", "summary", "rationale", "severity") if finding.get(k)}
    usr = (f"Diff under review:\n```\n{diff}\n```\n"
           f"A reviewer flagged this issue, NOT in the seeded answer key:\n"
           f"{json.dumps(payload, ensure_ascii=False)}\n"
           f"Is this a genuine defect actually present in the diff?")
    sysf = tempfile.mkstemp(suffix=".sys")[1]; usrf = tempfile.mkstemp(suffix=".usr")[1]
    open(sysf, "w").write(JUDGE_SYS); open(usrf, "w").write(usr)
    cmd = [MU, "ask", "--bare", "--provider", "openai-codex", "--model", model,
           "--append-system-prompt", sysf, "--prompt-file", usrf]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        out = ""
    finally:
        os.unlink(sysf); os.unlink(usrf)
    parsed, _ = rb.extract_json((out or "").strip()) if out else (None, "empty")
    return parsed.get("real") if isinstance(parsed, dict) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--judge-model", default="gpt-5.5")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--limit-fp", type=int, default=0, help="judge at most N distinct findings (0=all; for smoke tests)")
    ap.add_argument("--out", default="/tmp/pr-bench/adjudication.json")
    a = ap.parse_args()

    cases = {c["id"]: c for c in rb.load_cases(None)}
    recs = []
    for r in a.runs:
        recs += load(r)

    # distinct extra findings per case (dedup) -> judge each ONCE
    distinct = {}
    for r in recs:
        for f in r.get("score", {}).get("false_positives", []):
            if isinstance(f, dict):
                distinct.setdefault((r["case_id"], fblob(f)), f)
    items = list(distinct.items())
    if a.limit_fp:
        items = items[:a.limit_fp]

    verdict = {}
    for i, ((cid, blob), f) in enumerate(items):
        v = judge(a.judge_model, cases.get(cid, {}).get("diff", ""), f, a.timeout)
        verdict[(cid, blob)] = v
        print(f"[{i+1}/{len(items)}] {cid}: real={v}  {blob[:60]}", file=sys.stderr)

    # re-score (real extras don't count against precision)
    for r in recs:
        s = r["score"]; cid = r["case_id"]
        spurious = real_extra = 0
        for f in s.get("false_positives", []):
            v = verdict.get((cid, fblob(f))) if isinstance(f, dict) else False
            if v is True:
                real_extra += 1
            else:
                spurious += 1  # False or unjudged -> conservative
        matched = s.get("matched_count", 0)
        recall = matched / max(1, s.get("expected_count", 0))
        precision = matched / max(1, matched + spurious)
        severity = s.get("severity_points", 0) / max(1, matched)
        claim_pen = min(0.25, 0.10 * s.get("forbidden_claim_count", 0))
        r["adj"] = {"score": max(0.0, 0.50 * recall + 0.25 * precision + 0.25 * severity - claim_pen),
                    "orig": s.get("score"), "real_extra": real_extra, "spurious": spurious}

    by = defaultdict(list)
    for r in recs:
        by[r["model"]].append(r)
    rows = sorted(({"model": m, "n": len(rs),
                    "orig": sum((x["adj"]["orig"] or 0) for x in rs) / len(rs),
                    "adj": sum(x["adj"]["score"] for x in rs) / len(rs),
                    "real_extra": sum(x["adj"]["real_extra"] for x in rs)} for m, rs in by.items()),
                   key=lambda x: -x["adj"])
    print("\n=== adjudicated leaderboard ===")
    print("%-26s %4s %6s %6s %10s" % ("model", "n", "orig", "adj", "real_xtra"))
    for r in rows:
        print("%-26s %4d %6.3f %6.3f %10d" % (r["model"], r["n"], r["orig"], r["adj"], r["real_extra"]))

    fc = defaultdict(lambda: {"miss": 0, "n": 0, "real_extra": 0})
    for r in recs:
        cid = r["case_id"]; fc[cid]["n"] += 1
        fc[cid]["miss"] += 1 if r["score"].get("misses") else 0
        fc[cid]["real_extra"] += r["adj"]["real_extra"]
    print("\n=== failure cases (miss = models that missed the seeded bug) ===")
    for cid, d in sorted(fc.items(), key=lambda kv: -kv[1]["miss"] / max(1, kv[1]["n"])):
        print("%-46s miss %2d/%-2d  real-extra %d" % (cid, d["miss"], d["n"], d["real_extra"]))

    json.dump({"verdicts": {f"{k[0]}|{k[1][:48]}": v for k, v in verdict.items()},
               "leaderboard": rows,
               "failure_cases": {k: v for k, v in fc.items()}},
              open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
