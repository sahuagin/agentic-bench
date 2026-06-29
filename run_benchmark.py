#!/usr/bin/env python3
"""agentic-bench — tool-loop benchmark for local models through mu.

Drives the PRODUCTION agentic path (`mu ask --provider ollama --tools
read,grep` from a fixture repo cwd) and grades the final answer. The
answers are tool-gated: unreachable without actually executing the
read/grep loop, so this measures tool-call formation, faithful
tool-result use, multi-turn state, and termination — not codegen.

Sibling of code-review-bench (which measures single-shot review).
Born from the 2026-06-05 finding that qwen3-coder:30b's tool calls
flaked ~50% through this exact path (mu PR #179 added the rescue).

Usage:
  ./run_benchmark.py [--models M1,M2,...] [--reps N] [--cases cases.json]

Models run as groups (all reps of all cases per model) with a full
ollama eviction between groups — q8_0 35B + gpt-oss don't coexist in
48GB, and KEEP_ALIVE=24h otherwise pollutes timings with the previous
model's residency (code-review-bench NOTES.md gotcha).
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MU_BINARY = pathlib.Path.home() / "src/public_github/mu/target/release/mu"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_MODELS = ["gpt-oss:20b", "qwen3-coder:30b", "qwen3.6:35b-a3b-q8_0"]
RUN_TIMEOUT_S = 420  # cold loads of the 38GB q8_0 need headroom
LEAK_RE = re.compile(r"<function=|</?tool_call>")


def evict_all():
    """Unload every resident model (keep_alive: 0) for clean timings."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=30) as r:
            loaded = [m["name"] for m in json.load(r).get("models", [])]
        for name in loaded:
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"{OLLAMA}/api/generate", data=body,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=120).read()
        if loaded:
            print(f"  evicted: {', '.join(loaded)}", flush=True)
    except Exception as e:  # noqa: BLE001 — eviction is best-effort
        print(f"  evict_all: {e} (continuing)", flush=True)


def fixture_commit(repo: str) -> str:
    out = subprocess.run(
        ["jj", "log", "-r", "main", "--no-graph", "-T", "commit_id.short(12)"],
        cwd=repo, capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def run_case(model: str, repo: str, prompt: str) -> dict:
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            [str(MU_BINARY), "ask", "--bare", "--provider", "ollama", "--model", model,
             "--tools", "read,grep", prompt],
            cwd=repo, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        out, err, rc = p.stdout.strip(), p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "timeout", "wall_s": round(time.monotonic() - t0, 1)}
    wall = round(time.monotonic() - t0, 1)
    # Tool-call turn counts, from the daemon's stderr tracing if present.
    tool_calls = err.count("tool_call") if err else None
    return {"output": out, "exit": rc, "wall_s": wall}


def grade(case: dict, output: str) -> dict:
    correct = bool(re.search(case["answer_regex"], output, re.IGNORECASE))
    leak = bool(LEAK_RE.search(output))
    if case.get("negative") and not correct and not leak and output.strip():
        # A negative probe answered with substance but no admission of
        # absence = fabricated behavior for a nonexistent symbol — the
        # worst outcome; record it distinctly. (Empty output is a
        # plumbing failure, not fabrication.)
        return {"correct": False, "leak": leak, "fabricated": True}
    return {"correct": correct, "leak": leak, "fabricated": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cases", default=str(HERE / "cases.json"))
    args = ap.parse_args()

    spec = json.loads(pathlib.Path(args.cases).read_text())
    repo = str(pathlib.Path(spec["fixture_repo"]).expanduser())
    cases = spec["cases"]
    models = [m for m in args.models.split(",") if m]

    commit = fixture_commit(repo)
    if commit != spec["fixture_commit"]:
        print(f"WARNING: fixture repo at {commit}, cases pinned to "
              f"{spec['fixture_commit']} — answers may have drifted.", flush=True)

    results_path = HERE / "results" / f"results-{time.strftime('%Y-%m-%d-%H%M%S')}.jsonl"
    results_path.parent.mkdir(exist_ok=True)
    n_runs = len(models) * len(cases) * args.reps
    print(f"agentic-bench: {len(models)} models x {len(cases)} cases x "
          f"{args.reps} reps = {n_runs} runs -> {results_path.name}", flush=True)

    with results_path.open("w") as f:
        for model in models:
            print(f"\n=== {model} ===", flush=True)
            evict_all()
            for case in cases:
                for rep in range(1, args.reps + 1):
                    r = run_case(model, repo, case["prompt"])
                    g = grade(case, r.get("output", ""))
                    row = {"model": model, "case": case["id"],
                           "shape": case["shape"], "rep": rep,
                           "commit": commit, **r, **g}
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    mark = "ok" if g["correct"] else (
                        "LEAK" if g["leak"] else
                        "FABRICATED" if g["fabricated"] else "wrong")
                    print(f"  {case['id']} rep{rep}: {mark} ({r['wall_s']}s)",
                          flush=True)

    print(f"\nresults: {results_path}", flush=True)
    summarize(results_path)
    return 0


def summarize(path: pathlib.Path):
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    print(f"\n{'model':<28} {'score':>6} {'leaks':>6} {'fab':>4} {'med wall':>9}")
    for model in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == model]
        score = sum(r["correct"] for r in mine) / len(mine)
        leaks = sum(r["leak"] for r in mine)
        fab = sum(r["fabricated"] for r in mine)
        walls = sorted(r["wall_s"] for r in mine)
        med = walls[len(walls) // 2]
        print(f"{model:<28} {score:>6.3f} {leaks:>6} {fab:>4} {med:>8.1f}s")


if __name__ == "__main__":
    sys.exit(main())
