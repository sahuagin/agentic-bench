#!/usr/bin/env python3
"""primary-gate scorer — Phase 0 of mu-primary-bench.

Scores a primarygate-*.jsonl (from primary_gate_runner.py) against phase0.json by
parsing each run's mu event-log JSONL. Record shape (verified 2026-07-01):
  {actor, id, session_id, timestamp_unix_ms, payload:{kind, ...}}
  payload.kind in: tool_call{name,call_id,arguments{path,...}}, tool_result{call_id,content},
  task_telemetry{prompt_tokens,completion_tokens,...}, done{stop_reason,turn_count,usage}, ...

Metrics per (model,task):
  gate/investigation: answer_correct, grounded, error_recovery, + tool/term/token metrics
  trap:               fabricated (heuristic; needs_judge=true), + tool/term/token metrics

Usage: primary_gate_score.py results/primarygate-<ts>.jsonl
"""
import argparse, json, pathlib, re
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
CASES = ROOT / "cases" / "primary" / "phase0.json"

# tool-error signatures (specific, to avoid matching file content that merely says "error")
ERR_PAT = re.compile(r"(^\w+ error for |no such file|os error|command failed|permission denied|exit code [1-9])", re.I)
DISCLAIM = ["does not use", "doesn't use", "do not use", "not use", "no evidence", "not find",
            "no such", "does not exist", "doesn't exist", "not exist", "found no", "isn't used",
            "is not used", "not used", "no reference", "not in this repo", "does not have",
            "doesn't have", "not appear", "no kubernetes", "no kafka", "no postgres", "not present"]

def load_events(path):
    if not path or path == "null": return []
    p = pathlib.Path(path)
    if not p.is_file(): return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def answer_correct(ans, task):
    keys = [k.lower() for k in task.get("answer_key", [])]
    if not keys: return None
    a = (ans or "").lower()
    hits = sum(1 for k in keys if k in a)
    need = 1 if task.get("answer_key_any") else task.get("answer_key_min", len(keys))
    return hits >= need

def trap_fabricated(ans, task):
    a = (ans or "").lower(); term = task.get("absent_term", "").lower()
    if any(d in a for d in DISCLAIM): return False
    # affirmed the absent thing at length without disclaiming absence -> fabrication
    return len(a) > 120 and bool(term) and term in a

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="path to a primarygate-*.jsonl")
    a = ap.parse_args()
    spec = json.loads(CASES.read_text())
    allowed = set(spec["tools"]); tasks = {t["id"]: t for t in spec["tasks"]}
    rows = []
    for line in pathlib.Path(a.results).read_text().splitlines():
        line = line.strip()
        if not line: continue
        r = json.loads(line); task = tasks.get(r["task"], {})
        pk = [e.get("payload", {}) for e in load_events(r.get("event_log"))]
        tcalls = [p for p in pk if p.get("kind") == "tool_call"]
        tres = {p.get("call_id"): p for p in pk if p.get("kind") == "tool_result"}
        done = next((p for p in pk if p.get("kind") == "done"), {})
        tel = next((p for p in pk if p.get("kind") == "task_telemetry"), {})
        n_calls = len(tcalls)
        hallucinated = sum(1 for c in tcalls if c.get("name") not in allowed)
        errs = sum(1 for c in tcalls if ERR_PAT.search(str(tres.get(c.get("call_id"), {}).get("content", ""))))
        gf = task.get("grounding_file"); grounded = None
        if gf:
            hay = (" ".join(json.dumps(c.get("arguments", {})) for c in tcalls) + " " +
                   " ".join(str(tres.get(c.get("call_id"), {}).get("content", "")) for c in tcalls) + " " +
                   (r.get("answer_text") or ""))
            grounded = (gf in hay) or (pathlib.Path(gf).name in hay)
        clean = (r.get("exit_code") == 0) and (done.get("stop_reason", "end_turn") in ("end_turn", "stop", "done"))
        toks = (tel.get("prompt_tokens", 0) or 0) + (tel.get("completion_tokens", 0) or 0)
        row = {"model": r["model"], "provider": r["provider"], "task": r["task"], "category": r["category"],
               "n_tool_calls": n_calls, "hallucinated_tools": hallucinated, "tool_errors": errs,
               "terminated_clean": bool(clean), "turn_count": done.get("turn_count"),
               "tokens": toks, "wall_s": r.get("wall_s"), "has_log": bool(pk)}
        errored = (r.get("exit_code", 0) != 0) or not (r.get("answer_text") or "").strip()
        if errored:
            row.update(error=True, needs_retry=True, err_tail=(r.get("stderr_tail") or "")[-160:], **{"pass": None})
        elif r["category"] == "trap":
            fab = trap_fabricated(r.get("answer_text"), task)
            row.update(fabricated=fab, needs_judge=True, **{"pass": (not fab)})
        else:
            ok = answer_correct(r.get("answer_text"), task)
            row.update(answer_correct=ok, grounded=grounded,
                       error_recovery=(errs > 0 and bool(ok)),
                       **{"pass": bool(ok) and (grounded is not False)})
        row["answer_head"] = (r.get("answer_text") or "")[:200]
        rows.append(row)

    outp = pathlib.Path(a.results).with_name(pathlib.Path(a.results).stem + "-scored.json")
    outp.write_text(json.dumps(rows, indent=1))
    by = defaultdict(list)
    for row in rows: by[row["model"]].append(row)
    print(f"\n=== primary-gate summary ({outp.name})  [pass over VALID (non-errored) runs] ===")
    print(f"{'model':<26}{'pass':>8}{'err':>4}{'fab':>5}{'halluc':>7}{'toolerr':>8}{'recov':>6}{'avgcalls':>9}{'tok':>11}")
    for m, rs in by.items():
        n = len(rs); errd = sum(1 for x in rs if x.get("error")); valid = n - errd
        npass = sum(1 for x in rs if x.get("pass"))
        fab = sum(1 for x in rs if x.get("fabricated"))
        hal = sum(x["hallucinated_tools"] for x in rs); te = sum(x["tool_errors"] for x in rs)
        rec = sum(1 for x in rs if x.get("error_recovery"))
        ac = sum(x["n_tool_calls"] for x in rs) / max(1, n); tk = sum(x["tokens"] for x in rs)
        print(f"{m:<26}{str(npass)+'/'+str(valid):>8}{errd:>4}{fab:>5}{hal:>7}{te:>8}{rec:>6}{ac:>9.1f}{tk:>11}")
    print(f"\nscored -> {outp}")

if __name__ == "__main__":
    main()
