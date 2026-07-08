#!/usr/bin/env python3
"""primary-gate runner — Phase 0 of mu-primary-bench.

Runs each candidate model AS the mu agent on the Phase-0 task set (tool-use gate +
grounded investigation + fabrication traps) and records, per (model, task), the final
answer text + the session event-log path, for scoring by primary_gate_score.py.

FAITHFUL HARNESS (predicts real mu-primary behavior, not generic tool-use):
  [with-ollama-lease] mu ask --bare \
     --tools tool.read,tool.grep,tool.bash,tool.glob \
     --append-system-prompt cases/primary/role-primary.md \
     --provider <P> --model <M> "<task prompt>"
run with cwd = the mu repo so the tools operate on mu. Local (ollama) models are
wrapped in with-ollama-lease so a peer can't evict them mid-run.

Usage:
  primary_gate_runner.py --models "ollama:qwen3:8b,openai_codex:gpt-5.5" [--tasks G1,I1,T1] [--timeout 300]
  model spec = <provider>:<model> (first ':' splits); provider 'ollama' => leased.
"""
import argparse, json, os, subprocess, time, pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ROOT / "cases" / "primary" / "phase0.json"
ROLE = ROOT / "cases" / "primary" / "role-primary.md"
EVENTS_DIR = pathlib.Path.home() / ".local/share/mu/events"

def newest_eventlog(after_ns):
    """The event-log JSONL touched during this run = newest mtime strictly after `after_ns`."""
    best, best_m = None, after_ns
    for f in EVENTS_DIR.glob("*/*.jsonl"):
        try: m = f.stat().st_mtime_ns
        except OSError: continue
        if m > best_m:
            best, best_m = f, m
    return str(best) if best else None

def run_one(provider, model, task, tools_csv, repo_path, timeout, lease=True):
    cmd = ["mu", "ask", "--bare", "--tools", tools_csv,
           "--append-system-prompt", str(ROLE),
           "--provider", provider, "--model", model, task["prompt"]]
    if provider == "ollama" and lease:
        cmd = ["with-ollama-lease"] + cmd
    before = time.time_ns(); t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout + 30)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = ((e.stderr or "") if isinstance(e.stderr, str) else "") + "\n[TIMEOUT]"
        rc = 124
    wall = round(time.monotonic() - t0, 1)
    return {"answer_text": out.strip(), "stderr_tail": err[-800:], "exit_code": rc,
            "wall_s": wall, "event_log": newest_eventlog(before)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True,
                    help="comma list of <provider>:<model>, e.g. ollama:qwen3:8b,openai_codex:gpt-5.5,openrouter:z-ai/glm-5.2")
    ap.add_argument("--tasks", default="", help="comma list of task ids to run (default: all)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--no-lease", action="store_true",
                    help="do NOT wrap ollama calls in with-ollama-lease (caller holds it, e.g. one lease per model across its whole sweep)")
    a = ap.parse_args()

    spec = json.loads(CASES.read_text())
    tools_csv = ",".join(spec["tools"]); repo = os.path.expanduser(spec["repo_path"])
    tasks = spec["tasks"]
    if a.tasks:
        want = set(a.tasks.split(",")); tasks = [t for t in tasks if t["id"] in want]
    models = [(m.partition(":")[0], m.partition(":")[2]) for m in a.models.split(",") if m.strip()]

    ts = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "results" / f"primarygate-{ts}.jsonl"; out.parent.mkdir(exist_ok=True)
    print(f"primary-gate: {len(models)} models x {len(tasks)} tasks -> {out.name}", flush=True)
    with out.open("w") as fh:
        for prov, mod in models:
            for task in tasks:
                print(f"  {prov}:{mod}  {task['id']} ...", flush=True)
                r = run_one(prov, mod, task, tools_csv, repo, a.timeout, lease=not a.no_lease)
                rec = {"provider": prov, "model": mod, "task": task["id"],
                       "category": task["category"], **r}
                fh.write(json.dumps(rec) + "\n"); fh.flush()
                print(f"    rc={r['exit_code']} {r['wall_s']}s "
                      f"log={'ok' if r['event_log'] else 'MISSING'} chars={len(r['answer_text'])}", flush=True)
    print(f"done -> {out}")

if __name__ == "__main__":
    main()
