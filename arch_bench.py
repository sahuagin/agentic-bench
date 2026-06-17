#!/usr/bin/env python3
"""arch-bench — unified model benchmark: provider × model × task-type × context × cost.

Extends agentic-bench from a single-task/single-provider/single-context tool-loop
probe into a sweep over {OpenRouter + ollama} × {rust, python} × {agentic, coding,
review} × {context sizes} that records speed, raw outputs (for later grading),
token usage, and cost. Grading is a SEPARATE step (arch_score.py) so a crash or a
deadline/budget cutoff mid-sweep never loses collected data.

Backends, hybrid by task-type:
  - agentic : `mu ask --tools read,grep,ls,glob` from a fixture cwd — the production
              agentic loop (the crown agentic-bench was built to measure). Native ctx.
  - coding  : direct single-shot HTTP. ollama /api/chat (options.num_ctx swept),
    review    OpenRouter /v1/chat/completions (usage tokens → cost). Prompt is padded
              to the target context budget so context-size impact is measurable.

Concurrency: the ollama box (one GPU) runs serially, grouped by model with an
eviction between models (they don't co-fit in 48GB), but that whole track runs
CONCURRENTLY with a parallel OpenRouter pool. OpenRouter single-shot calls return
usage inline (thread-safe), so they fan out; mu-driven agentic calls serialize on a
lock (the telemetry read is process-global).

Guards (both hard, thread-safe):
  --max-usd        cumulative OpenRouter spend cap (ollama is free).
  --deadline-min   wall-clock deadline; stops cleanly and writes what's collected.

Usage:
  ./arch_bench.py --max-usd 50 --deadline-min 360        # full sweep
  ./arch_bench.py --smoke                                 # tiny free+~$0.05 validation
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MU = pathlib.Path.home() / "src/public_github/mu/target/release/mu"
MU_EVENTS = pathlib.Path.home() / ".local/share/mu/events"
CFG = json.loads((HERE / "config_models.json").read_text())
CASES = HERE / "arch_cases"
RESULTS = HERE / "arch_results"
THINK_RE = re.compile(r"\[thinking\].*?(?:\[/thinking\]|\n\n)", re.DOTALL)
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
AGENTIC_FIXTURE = {"rust": str(pathlib.Path.home() / "src/public_github/mu"),
                   "python": str(pathlib.Path.home() / "src/public_github/mu-analytics")}

_EMIT_LOCK = threading.Lock()
_MU_LOCK = threading.Lock()   # mu_agentic telemetry read is process-global
_CORPUS: "str | None" = None


# ---------------------------------------------------------------- padding
def corpus() -> str:
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS
    roots = [pathlib.Path.home() / "src/public_github/mu/crates",
             pathlib.Path.home() / "src/public_github/mu-analytics"]
    chunks, total = [], 0
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.rs")) + sorted(root.rglob("*.py")):
            try:
                t = p.read_text(errors="ignore")
            except OSError:
                continue
            chunks.append(f"// === {p.name} ===\n{t}\n")
            total += len(t)
            if total > 1_200_000:
                break
        if total > 1_200_000:
            break
    blob = "\n".join(chunks)
    while len(blob) < 600_000 and blob:
        blob += "\n" + blob
    _CORPUS = blob
    return _CORPUS


def est_tokens(s: str) -> int:
    return len(s) // 4


def build_prompt(task: str, target_ctx: int) -> str:
    budget_chars = int(target_ctx * 4 * 0.80)
    pad_chars = max(0, budget_chars - len(task) - 400)
    pad = corpus()[:pad_chars]
    if not pad:
        return task
    return (f"<reference_material>\n{pad}\n</reference_material>\n\n"
            f"# TASK\nAnswer the task below. The reference material above is context "
            f"you may use or ignore.\n\n{task}")


def strip_thinking(text: str) -> str:
    return THINK_RE.sub("", text).strip()


# ---------------------------------------------------------------- backends
def ollama_chat(model, prompt, num_ctx, max_tok, think) -> dict:
    base = CFG["ollama"]["endpoint"]
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "options": {"num_ctx": num_ctx, "num_predict": max_tok}}
    if think:
        body["think"] = True
    t0 = time.monotonic()
    req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.load(r)
    wall = round(time.monotonic() - t0, 1)
    msg = d.get("message", {})
    pe, ec = d.get("prompt_eval_count"), d.get("eval_count")
    ped, ed = d.get("prompt_eval_duration"), d.get("eval_duration")
    return {"answer": (msg.get("content") or "").strip(),
            "thinking_chars": len(msg.get("thinking") or ""),
            "input_tok": pe, "output_tok": ec, "wall_s": wall,
            "gen_tps": round(ec / (ed / 1e9), 1) if ec and ed else None,
            "prompt_tps": round(pe / (ped / 1e9), 1) if pe and ped else None,
            "exit_reason": "done"}


def openrouter_chat(model, prompt, max_tok) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tok, "usage": {"include": True}}
    req = urllib.request.Request(
        CFG["openrouter"]["endpoint"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {OR_KEY}", "X-Title": "arch-bench"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    wall = round(time.monotonic() - t0, 1)
    ch = (d.get("choices") or [{}])[0].get("message", {})
    usage = d.get("usage") or {}
    it, ot = usage.get("prompt_tokens"), usage.get("completion_tokens")
    return {"answer": (ch.get("content") or "").strip(),
            "thinking_chars": len(ch.get("reasoning") or ""),
            "input_tok": it, "output_tok": ot, "wall_s": wall,
            "gen_tps": round(ot / wall, 1) if ot and wall else None,
            "prompt_tps": None, "exit_reason": "done"}


# Conservative allowance (tokens) for the read/grep/ls/glob tool loop's hidden
# input on cloud agentic runs — mu's --bare path persists no telemetry, so we
# cannot meter the files the loop pulled in. Over-estimate so the $ cap is SAFE
# (we'd rather stop early than overspend). Tunable via env.
AGENTIC_TOOL_INPUT_ALLOWANCE = int(os.environ.get("ARCH_AGENTIC_TOOL_TOK", "12000"))


def mu_agentic(provider, model, prompt, cwd, timeout) -> dict:
    with _MU_LOCK:  # serialize the subprocess (one mu daemon at a time)
        t0 = time.monotonic()
        try:
            p = subprocess.run(
                [str(MU), "ask", "--bare", "--provider", provider, "--model", model,
                 "--tools", "read,grep,ls,glob", prompt],
                cwd=cwd, capture_output=True, text=True, timeout=timeout)
            out = strip_thinking(p.stdout)
            exit_reason = "done" if p.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            return {"answer": "", "wall_s": round(time.monotonic() - t0, 1),
                    "exit_reason": "timeout", "input_tok": None, "output_tok": None,
                    "tok_estimated": False}
        wall = round(time.monotonic() - t0, 1)
    # --bare persists no event log → estimate. Conservative for the cap.
    in_est = len(prompt) // 4 + AGENTIC_TOOL_INPUT_ALLOWANCE
    out_est = max(1, len(out) // 4)
    return {"answer": out, "wall_s": wall, "exit_reason": exit_reason,
            "input_tok": in_est, "output_tok": out_est, "tok_estimated": True}


def warmup_ollama(model, num_ctx):
    """Untimed load of `model` into VRAM so cold-load doesn't count against a
    scored run's timeout (eviction unloads the prior model; the next group's
    first run would otherwise absorb the full 50-65GB load). One trivial call."""
    base = CFG["ollama"]["endpoint"]
    body = {"model": model, "messages": [{"role": "user", "content": "ok"}],
            "stream": False, "options": {"num_ctx": num_ctx, "num_predict": 1}}
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1800).read()
        print(f"    warmed {model} ({round(time.monotonic()-t0)}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"    warmup {model}: {e} (continuing)", flush=True)


def evict_ollama():
    base = CFG["ollama"]["endpoint"]
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=30) as r:
            loaded = [m["name"] for m in json.load(r).get("models", [])]
        for name in loaded:
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            req = urllib.request.Request(f"{base}/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=120).read()
        if loaded:
            print(f"    evicted: {', '.join(loaded)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"    evict: {e} (continuing)", flush=True)


def cloud_cost(model, it, ot) -> float:
    m = CFG["openrouter"]["models"].get(model, {})
    if not it or not ot:
        return 0.0
    return round(it / 1e6 * m.get("in", 0) + ot / 1e6 * m.get("out", 0), 5)


# ---------------------------------------------------------------- jobs
_CASE_CAP: "int | None" = None  # set by --limit-cases / --smoke


def load_cases(task, lang) -> list:
    p = CASES / f"{task}_{lang}.json"
    cs = json.loads(p.read_text()) if p.exists() else []
    return cs[:_CASE_CAP] if _CASE_CAP else cs


def case_prompt(task, case) -> str:
    if task == "agentic":
        return case["prompt"]
    if task == "review":
        return (case.get("instructions", "") + "\n\n" + case.get("diff", ""))
    return case.get("prompt") or case.get("instructions", "")


def build_jobs(models_ollama, models_or, ctx_local, ctx_cloud, reps_ollama, reps_cloud):
    """Yield job dicts. agentic = native ctx; coding/review swept over ctx tiers."""
    jobs = []
    for provider, models, tiers, reps in (
            ("ollama", models_ollama, ctx_local, reps_ollama),
            ("openrouter", models_or, ctx_cloud, reps_cloud)):
        for model in models:
            # agentic (native ctx)
            for lang in ("rust", "python"):
                for case in load_cases("agentic", lang):
                    for rep in range(1, reps + 1):
                        jobs.append({"provider": provider, "model": model,
                                     "task_type": "agentic", "lang": lang,
                                     "case": case, "ctx": None, "rep": rep})
            # single-shot, ascending ctx. Full case set only at the base (smallest)
            # tier; larger tiers run a probe subset so the context-curve is measured
            # without N_cases × N_tiers blowup (a 131k-ctx local run is minutes of
            # prompt-eval each).
            base = min(tiers) if tiers else None
            probe = CFG.get("ctx_probe_cap", 2)
            for ctx in tiers:
                cap = None if ctx == base else probe
                for task in ("coding", "review"):
                    for lang in ("rust", "python"):
                        cases = load_cases(task, lang)
                        if cap:
                            cases = cases[:cap]
                        for case in cases:
                            for rep in range(1, reps + 1):
                                jobs.append({"provider": provider, "model": model,
                                             "task_type": task, "lang": lang,
                                             "case": case, "ctx": ctx, "rep": rep})
    return jobs


def execute(job) -> dict:
    provider, model, task = job["provider"], job["model"], job["task_type"]
    case = job["case"]
    if task == "agentic":
        r = mu_agentic(provider, model, case_prompt(task, case),
                       AGENTIC_FIXTURE[job["lang"]], timeout=600)
    else:
        think = CFG["ollama"]["models"].get(model, {}).get("thinks", False)
        ntok = 8192 if think else CFG["max_output_tokens"]
        prompt = build_prompt(case_prompt(task, case), job["ctx"])
        if provider == "ollama":
            cap = min(job["ctx"], CFG["ollama"]["models"][model]["ctx"])
            r = ollama_chat(model, prompt, cap, ntok, think)
        else:
            r = openrouter_chat(model, prompt, ntok)
        r["prompt_est_tok"] = est_tokens(prompt)
    cost = cloud_cost(model, r.get("input_tok"), r.get("output_tok")) \
        if provider == "openrouter" else 0.0
    row = {"ts": int(time.time()), "provider": provider, "model": model,
           "task_type": task, "lang": job["lang"], "case": case["id"],
           "shape": case.get("shape"), "ctx_target": job["ctx"], "rep": job["rep"],
           "answer_regex": case.get("answer_regex"),
           "negative": case.get("negative", False),
           "cost_usd": cost,
           "cost_kind": "billed" if provider == "openrouter" else "free"}
    for k in ("expected_findings", "forbidden_claims", "judge_rubric",
              "max_points", "pattern", "grader", "pytest"):
        if k in case:
            row[k] = case[k]
    row.update(r)
    return row


def model_size(model):
    s = CFG["ollama"]["models"].get(model, {}).get("params", "0B")
    try:
        return float(s.rstrip("B"))
    except ValueError:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=50.0)
    ap.add_argument("--deadline-min", type=float, default=600.0)
    ap.add_argument("--reps-ollama", type=int, default=CFG.get("reps_ollama", 1))
    ap.add_argument("--reps-cloud", type=int, default=CFG.get("reps_cloud", 2))
    ap.add_argument("--or-concurrency", type=int, default=6)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--ollama-only", action="store_true")
    ap.add_argument("--cloud-only", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    global _CASE_CAP
    if args.limit_cases:
        _CASE_CAP = args.limit_cases

    if args.smoke:
        _CASE_CAP = 1
        models_ollama, models_or = ["qwen3-coder:30b"], ["deepseek/deepseek-v4-pro"]
        ctx_local, ctx_cloud = [8192], [8192]
        reps_ollama = reps_cloud = 1
        deadline = time.time() + 25 * 60
    else:
        # ollama ordered by architectural interest (curated) so the spread is
        # covered early; the deadline truncates whole models at the tail.
        order = CFG.get("ollama_order") or list(CFG["ollama"]["models"])
        models_ollama = [m for m in order if m in CFG["ollama"]["models"]]
        models_or = sorted(CFG["openrouter"]["models"],
                           key=lambda m: CFG["openrouter"]["models"][m]["in"])  # cheap first
        ctx_local, ctx_cloud = (CFG["context_sizes_local"], CFG["context_sizes_cloud"])
        reps_ollama, reps_cloud = args.reps_ollama, args.reps_cloud
        deadline = time.time() + args.deadline_min * 60
    if args.ollama_only:
        models_or = []
    if args.cloud_only:
        models_ollama = []

    RESULTS.mkdir(exist_ok=True)
    stamp = args.tag or time.strftime("%Y-%m-%d-%H%M%S")
    out_path = RESULTS / f"raw-{stamp}.jsonl"
    spend_path = RESULTS / f"spend-{stamp}.json"
    spent = {"cloud_usd": 0.0, "runs": 0, "skipped_cap": 0, "errors": 0}
    fh = out_path.open("a")

    def emit(row):
        with _EMIT_LOCK:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            spent["runs"] += 1
            if row.get("cost_usd"):
                spent["cloud_usd"] = round(spent["cloud_usd"] + row["cost_usd"], 5)
            if row.get("exit_reason") == "error":
                spent["errors"] += 1
            spend_path.write_text(json.dumps(spent))

    def past_deadline():
        return time.time() >= deadline

    all_jobs = build_jobs(models_ollama, models_or, ctx_local, ctx_cloud,
                          reps_ollama, reps_cloud)
    oll_jobs = [j for j in all_jobs if j["provider"] == "ollama"]
    or_jobs = [j for j in all_jobs if j["provider"] == "openrouter"]
    # cheap-model & small-ctx first so a cap/deadline cut truncates the costly tail
    or_jobs.sort(key=lambda j: (CFG["openrouter"]["models"][j["model"]]["in"],
                                j["task_type"] != "agentic", j["ctx"] or 0))
    print(f"arch-bench {stamp}: {len(oll_jobs)} ollama + {len(or_jobs)} openrouter runs | "
          f"cap ${args.max_usd} | deadline {args.deadline_min}min | "
          f"or-concurrency {args.or_concurrency} -> {out_path.name}", flush=True)

    def run_one(job, kind):
        try:
            row = execute(job)
        except Exception as e:  # noqa: BLE001
            row = {"ts": int(time.time()), "provider": job["provider"],
                   "model": job["model"], "task_type": job["task_type"],
                   "lang": job["lang"], "case": job["case"]["id"],
                   "ctx_target": job["ctx"], "rep": job["rep"],
                   "exit_reason": "error", "error": str(e)[:300],
                   "cost_usd": 0.0, "wall_s": None}
        emit(row)
        c = job["case"]["id"]
        print(f"  [{kind}] {job['model']} {job['lang']}/{c} "
              f"{job['task_type']} c{job['ctx']} r{job['rep']}: "
              f"{row.get('exit_reason')} {row.get('wall_s')}s "
              f"${spent['cloud_usd']:.2f}", flush=True)

    # ---- ollama track: serial, grouped by model, evict between models
    def ollama_track():
        cur = None
        for job in sorted(oll_jobs, key=lambda j: (model_size(j["model"]),
                                                   j["task_type"] != "agentic",
                                                   j["ctx"] or 0)):
            if past_deadline():
                print("  [ollama] deadline reached — stopping track", flush=True)
                break
            if job["model"] != cur:
                evict_ollama()
                cur = job["model"]
                print(f"  [ollama] === {cur} ===", flush=True)
                # warm at the largest ctx this model will be asked at, so the
                # cold-load (and VRAM sizing) is paid once, untimed.
                warm_ctx = min(max(ctx_local), CFG["ollama"]["models"][cur]["ctx"])
                warmup_ollama(cur, warm_ctx)
            run_one(job, "ollama")
        evict_ollama()

    # ---- openrouter track: parallel pool, cap-aware (check before submit)
    def openrouter_track():
        with cf.ThreadPoolExecutor(max_workers=args.or_concurrency) as ex:
            futs = []
            for job in or_jobs:
                if past_deadline():
                    print("  [or] deadline reached — no more submissions", flush=True)
                    break
                with _EMIT_LOCK:
                    if spent["cloud_usd"] >= args.max_usd:
                        spent["skipped_cap"] += 1
                        continue
                futs.append(ex.submit(run_one, job, "or"))
            cf.wait(futs)

    t_oll = threading.Thread(target=ollama_track, name="ollama")
    t_or = threading.Thread(target=openrouter_track, name="openrouter")
    t_oll.start()
    t_or.start()
    t_oll.join()
    t_or.join()

    fh.close()
    print(f"\nDONE: {spent['runs']} runs, ${spent['cloud_usd']:.2f} cloud spend, "
          f"{spent['skipped_cap']} skipped(cap), {spent['errors']} errors -> {out_path}",
          flush=True)
    evict_ollama()  # leave ollama clean but RUNNING (server untouched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
