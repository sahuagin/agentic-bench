#!/usr/bin/env python3
"""arch-score — grade arch-bench raw runs and emit leaderboards + cost.

Reads arch_results/raw-*.jsonl (collected by arch_bench.py) and scores each row
by task_type, then writes a scored jsonl + per-task-type leaderboards and a cost
report. Grading is separate from collection so it is cheap, deterministic, and
re-runnable without re-spending.

Graders by task_type:
  agentic : tool-gated regex match on the answer (+ negative/hallucination flag),
            mirroring agentic-bench grade().
  coding  : grader=="pytest" -> append the case's asserts to the model's code and
            run in a subprocess sandbox (pass/fail). grader=="judge" (default) ->
            a blind LLM judge from a DIFFERENT family (config judge_model, local =
            free) scores against the case rubric; we record points/max_points.
  review  : recall / precision / severity over expected_findings keywords, the
            code-review-bench rubric: 0.50*recall + 0.25*precision + 0.25*severity
            - min(0.25, 0.10*forbidden_claims).

Usage:
  ./arch_score.py [raw-*.jsonl ...]      # default: newest raw-*.jsonl
  ./arch_score.py --no-judge             # skip judge calls (coding judge rows -> ungraded)
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "arch_results"
CFG = json.loads((HERE / "config_models.json").read_text())
THINK_RE = re.compile(r"\[thinking\].*?(?:\[/thinking\]|\n\n)", re.DOTALL)
LEAK_RE = re.compile(r"<function=|</?tool_call>")
JUDGE_MODEL = CFG.get("judge_model", "gpt-oss:120b-code128k")
OLLAMA = CFG["ollama"]["endpoint"]
SEV_RANK = {"blocker": 3, "major": 2, "minor": 1, "nit": 0}


def strip_thinking(t: str) -> str:
    return THINK_RE.sub("", t or "").strip()


# ---------------------------------------------------------------- agentic
def grade_agentic(row) -> dict:
    out = strip_thinking(row.get("answer", ""))
    rx = row.get("answer_regex") or ""
    correct = bool(re.search(rx, out, re.IGNORECASE)) if rx else False
    leak = bool(LEAK_RE.search(out))
    fabricated = False
    if row.get("negative") and not correct and not leak and out.strip():
        fabricated = True
    return {"score": 1.0 if correct else 0.0, "correct": correct,
            "leak": leak, "fabricated": fabricated, "max_points": 1, "points": int(correct)}


# ---------------------------------------------------------------- coding (pytest)
def extract_code(answer: str) -> str:
    """Pull the fenced code block (last python/rust block) else the whole answer."""
    a = strip_thinking(answer)
    blocks = re.findall(r"```(?:python|rust|py|rs)?[^\n]*\n(.*?)```", a, re.DOTALL)
    return blocks[-1] if blocks else a


def grade_pytest(row) -> dict:
    code = extract_code(row.get("answer", ""))
    snippet = row.get("pytest", "")
    prog = code + "\n\n# --- appended test ---\n" + snippet
    try:
        p = subprocess.run([sys.executable, "-c", prog],
                           capture_output=True, text=True, timeout=30)
        ok = p.returncode == 0 and "OK" in p.stdout
        err = "" if ok else (p.stderr or p.stdout)[-400:]
    except subprocess.TimeoutExpired:
        ok, err = False, "exec timeout"
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)[:300]
    mp = row.get("max_points", 12)
    return {"score": 1.0 if ok else 0.0, "passed": ok, "points": mp if ok else 0,
            "max_points": mp, "grader": "pytest", "exec_err": err}


# ---------------------------------------------------------------- coding (judge)
def judge_call(rubric: str, task_prompt: str, answer: str) -> dict:
    sys_p = ("You are a strict code-grading judge. Score the SUBMISSION against the "
             "RUBRIC. The rubric lists criteria each prefixed with a [weight]. Award "
             "the full weight only if the criterion is clearly met, else 0 (no partial "
             "unless the criterion says so). Reply ONLY with JSON: "
             '{"points": <int>, "max_points": <int>, "notes": "<one line>"}.')
    user = (f"# RUBRIC\n{rubric}\n\n# TASK GIVEN TO MODEL\n{task_prompt}\n\n"
            f"# SUBMISSION\n{extract_code(answer)}\n\n"
            "Score it. JSON only.")
    body = {"model": JUDGE_MODEL,
            "messages": [{"role": "system", "content": sys_p},
                         {"role": "user", "content": user}],
            "stream": False, "options": {"num_ctx": 32768, "num_predict": 1024},
            "format": "json"}
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    txt = (d.get("message", {}) or {}).get("content", "") or ""
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    obj = json.loads(m.group(0)) if m else {}
    return obj


def grade_judge(row, do_judge=True) -> dict:
    mp = row.get("max_points", 12)
    if not do_judge or not row.get("answer", "").strip():
        return {"score": None, "points": None, "max_points": mp, "grader": "judge",
                "judged": False}
    try:
        obj = judge_call(row.get("judge_rubric", ""), "", row.get("answer", ""))
        pts = int(obj.get("points", 0))
        jmp = int(obj.get("max_points", mp)) or mp
        pts = max(0, min(pts, jmp))
        return {"score": round(pts / jmp, 3), "points": pts, "max_points": jmp,
                "grader": "judge", "judge_notes": obj.get("notes", ""), "judged": True}
    except Exception as e:  # noqa: BLE001
        return {"score": None, "points": None, "max_points": mp, "grader": "judge",
                "judged": False, "judge_err": str(e)[:200]}


# ---------------------------------------------------------------- review
def grade_review(row) -> dict:
    review = strip_thinking(row.get("answer", "")).lower()
    expected = row.get("expected_findings", []) or []
    forbidden = row.get("forbidden_claims", []) or []
    matched, missed = 0, []
    for ef in expected:
        kws = [k.lower() for k in ef.get("keywords", [])]
        hits = sum(1 for k in kws if k in review)
        fileref = (ef.get("file", "").lower() in review)
        if (fileref and hits >= 2) or hits >= 3:
            matched += 1
        else:
            missed.append(ef.get("id"))
    n_expected = len(expected) or 1
    recall = matched / n_expected
    # crude finding-count: lines that look like a finding (file: or severity word)
    claimed = len(re.findall(r"(blocker|major|minor|critical|severity|finding)", review))
    fp = max(0, claimed - matched) if claimed else 0
    precision = matched / max(matched + fp, 1) if (matched + fp) else 0.0
    # severity calibration: reward presence of severity language overall (coarse,
    # since we can't reliably align each claimed finding to an expected one here)
    sev = 1.0 if any(s in review for s in ("blocker", "major", "minor")) else 0.0
    fc = sum(1 for c in forbidden if c.lower() in review)
    score = (0.50 * recall + 0.25 * precision + 0.25 * sev
             - min(0.25, 0.10 * fc))
    return {"score": round(max(0.0, score), 3), "recall": round(recall, 3),
            "precision": round(precision, 3), "matched": matched,
            "n_expected": len(expected), "false_pos_est": fp,
            "forbidden_claims": fc, "missed": missed, "max_points": 1.0}


# ---------------------------------------------------------------- main
def grade_row(row, do_judge=True) -> dict:
    t = row.get("task_type")
    if row.get("exit_reason") in ("timeout", "error"):
        return {"score": None, "ungraded": row.get("exit_reason")}
    if t == "agentic":
        return grade_agentic(row)
    if t == "review":
        return grade_review(row)
    if t == "coding":
        return grade_pytest(row) if row.get("grader") == "pytest" else grade_judge(row, do_judge)
    return {"score": None, "ungraded": "unknown-task"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="*")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    paths = [pathlib.Path(p) for p in args.raw] or \
        sorted(RESULTS.glob("raw-*.jsonl"), key=lambda p: p.stat().st_mtime)[-1:]
    if not paths:
        print("no raw-*.jsonl found", flush=True)
        return 1
    rows = []
    for p in paths:
        for ln in p.read_text().splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    print(f"scoring {len(rows)} rows from {', '.join(p.name for p in paths)}", flush=True)

    scored = []
    for i, row in enumerate(rows):
        g = grade_row(row, do_judge=not args.no_judge)
        scored.append({**row, "grade": g})
        if (i + 1) % 50 == 0:
            print(f"  scored {i+1}/{len(rows)}", flush=True)

    stamp = args.tag or time.strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS / f"scored-{stamp}.jsonl"
    with out.open("w") as f:
        for s in scored:
            f.write(json.dumps(s) + "\n")
    print(f"\nscored -> {out}", flush=True)
    leaderboards(scored)
    return 0


def leaderboards(scored):
    def agg(rows, key):
        d = {}
        for r in rows:
            d.setdefault(r[key], []).append(r)
        return d

    for task in ("agentic", "coding", "review"):
        trows = [r for r in scored if r.get("task_type") == task
                 and r["grade"].get("score") is not None]
        if not trows:
            continue
        print(f"\n=== {task.upper()} leaderboard (n={len(trows)}) ===")
        print(f"{'model':<34}{'score':>7}{'n':>5}{'cost$':>9}{'med_s':>8}")
        for model, rs in sorted(agg(trows, "model").items(),
                                key=lambda kv: -sum(x['grade']['score'] for x in kv[1]) / len(kv[1])):
            sc = sum(x["grade"]["score"] for x in rs) / len(rs)
            cost = sum(x.get("cost_usd") or 0 for x in rs)
            walls = sorted(x.get("wall_s") or 0 for x in rs)
            med = walls[len(walls) // 2] if walls else 0
            print(f"{model:<34}{sc:>7.3f}{len(rs):>5}{cost:>9.3f}{med:>8.1f}")

    # context curve (single-shot tasks): score vs ctx per model
    ss = [r for r in scored if r.get("ctx_target") and r["grade"].get("score") is not None]
    if ss:
        print("\n=== CONTEXT CURVE (score by ctx_target, single-shot) ===")
        ctxs = sorted({r["ctx_target"] for r in ss})
        print(f"{'model':<34}" + "".join(f"{c:>10}" for c in ctxs))
        by_model = {}
        for r in ss:
            by_model.setdefault(r["model"], {}).setdefault(r["ctx_target"], []).append(r["grade"]["score"])
        for model, cd in sorted(by_model.items()):
            cells = []
            for c in ctxs:
                v = cd.get(c)
                cells.append(f"{sum(v)/len(v):>10.3f}" if v else f"{'-':>10}")
            print(f"{model:<34}" + "".join(cells))

    total_cost = sum(r.get("cost_usd") or 0 for r in scored)
    n_err = sum(1 for r in scored if r["grade"].get("ungraded"))
    print(f"\ntotal cloud cost ${total_cost:.2f} | ungraded(err/timeout) {n_err}")


if __name__ == "__main__":
    sys.exit(main())
