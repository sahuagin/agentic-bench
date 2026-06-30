#!/usr/bin/env python3
"""Hardcoding case (mu-8puo.1) — case (a), "does the model write crappy code?".
Each model is asked to make action_recall's danger-verb triggers data-driven; it
edits a jj workspace at mu HEAD (where the hardcoded const lives). A FIXED
invariant-lens grader (gpt-5.5) reads the diff and rules data_driven / partial /
hardcoded — no cargo (operator chose lens grading). Tests the operator's #1
recurring pain: do models retire the hardcoded const, or keep hardcoding?

Pre-warms ollama models; empty/timeout dispatch = LOAD failure (never scored).
jj workspace per model (forget + rm). Reuses review_runner's mu_ask + parser.
"""
import sys, json, subprocess, pathlib, time, argparse, os
HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); import review_runner as rr
MU = "/home/tcovert/src/public_github/mu"
SPEC = (ROOT / "cases/coding/spec-hardcode-dangerverbs.md").read_text()
_ws = ROOT / "cases/coding/worker-sys.md"
WORKER_SYS = _ws.read_text() if _ws.exists() else "You are a senior Rust engineer. Implement the task by editing the repo files. Make the change idiomatic, complete, and consistent with the codebase."
LANES = {"ollama": "ollama", "codex": "openai-codex", "openrouter": "openrouter"}
GRADER = "codex:gpt-5.5"
GRADE_SYS = (
    "You check a code change against ONE invariant: the danger-verb trigger table must be DATA-DRIVEN — "
    "the live table loaded from an external source (etcd or a config file) with the in-binary const only as a "
    "SEED FALLBACK, fail-open when the source is down. It must NOT remain a hardcoded compile-time const as the "
    "SOLE/live source. Read the diff and rule. Output ONLY JSON: "
    '{"verdict":"data_driven|partial|hardcoded","evidence":"<=200 chars"}. '
    "data_driven = live table from an external source + seed fallback + fail-open. "
    "hardcoded = a const is still the sole/live source (no external source wired in). "
    "partial = some movement (a config struct / loader sketched) but the const is still the live source OR no fail-open.")

def sh(*a, cwd=None, timeout=120):
    try:
        return subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        class R: returncode, stdout, stderr = 124, "", "timeout"
        return R()

def jj(*a, timeout=120): return sh("jj", "-R", MU, *a, timeout=timeout)

def prewarm(model, t):
    import tempfile; fd, of = tempfile.mkstemp(suffix=".out"); os.close(fd)
    try:
        with open(of, "w") as fh:
            subprocess.run(["timeout", "-k", "15", "-s", "TERM", str(t), "mu", "ask", "--bare",
                            "--provider", "ollama", "--model", model, "Reply with exactly: ready"],
                           stdout=fh, stderr=subprocess.DEVNULL, timeout=t + 30)
        return bool(open(of, errors="replace").read().strip())
    except Exception:
        return False
    finally:
        try: os.unlink(of)
        except Exception: pass

def dispatch(lane, model, prompt, wt, timeout, thinking=""):
    import tempfile; fd, of = tempfile.mkstemp(suffix=".out"); os.close(fd)
    cmd = ["timeout", "-k", "20", "-s", "TERM", str(timeout), "mu", "ask", "--bare",
           "--provider", LANES[lane], "--model", model, "--tools", "read,edit,grep,glob"]
    if thinking:
        cmd += ["--thinking", thinking]
    cmd.append(prompt)
    try:
        with open(of, "w") as fh:
            r = subprocess.run(cmd, cwd=wt, stdout=fh, stderr=subprocess.DEVNULL, timeout=timeout + 40)
        return r.returncode
    except subprocess.TimeoutExpired:
        return 124
    finally:
        try: os.unlink(of)
        except Exception: pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--worker-timeout", type=int, default=900)
    ap.add_argument("--load-timeout", type=int, default=300)
    ap.add_argument("--grade-timeout", type=int, default=240)
    ap.add_argument("--thinking", default="", help="pass --thinking <level> to the worker (low/none) to test the reasoning lever")
    a = ap.parse_args()
    cbench = os.path.expanduser("~/cbench"); os.makedirs(cbench, exist_ok=True)
    base = sh("git", "-C", MU, "rev-parse", "HEAD").stdout.strip()
    ts = time.strftime("%Y%m%d-%H%M%S"); out = ROOT / "results" / f"hardcode-{ts}.jsonl"; out.parent.mkdir(exist_ok=True)
    print(f"hardcode case (mu-8puo.1) @ mu {base[:8]}; grader={GRADER}; models={a.models}", flush=True)
    glane, _, gmodel = GRADER.partition(":")
    rows = []
    with out.open("w") as fh:
        for spec_model in a.models:
            lane, _, model = spec_model.partition(":")
            if lane == "ollama" and not prewarm(model, a.load_timeout):
                print(f"  {spec_model:<26} LOAD_FAIL (not scored)", flush=True)
                fh.write(json.dumps({"model": spec_model, "verdict": "load_fail", "score": None}) + "\n"); fh.flush(); continue
            name = "hc_" + spec_model.replace(":", "_").replace("/", "_").replace(".", "")
            wt = f"{cbench}/{name}"
            jj("workspace", "forget", name); sh("rm", "-rf", wt)
            add = jj("workspace", "add", "--name", name, wt, "-r", base)
            if add.returncode != 0:
                print(f"  {spec_model:<26} WS_FAIL {add.stderr.strip()[:80]}", flush=True); continue
            t0 = time.monotonic()
            rc = dispatch(lane, model, WORKER_SYS + "\n\n# TASK\n" + SPEC, wt, a.worker_timeout, a.thinking)
            diff = sh("jj", "diff", "--git", cwd=wt).stdout
            (pathlib.Path(cbench) / f"{name}.diff").write_text(diff)
            if diff.strip():
                gp = f"{GRADE_SYS}\n\nDIFF:\n{diff[:24000]}"
                graw, gw, _ = rr.mu_ask(LANES.get(glane, glane), gmodel, gp, a.grade_timeout)
                gj = rr.parse_json(graw) if graw else None
                verdict = gj.get("verdict", "unparseable") if isinstance(gj, dict) else "unparseable"
                evidence = (gj.get("evidence", "") if isinstance(gj, dict) else "")
            else:
                verdict, evidence, gw = "empty_diff", "", 0
            score = {"data_driven": 1.0, "partial": 0.5, "hardcoded": 0.0}.get(verdict)
            wall = round(time.monotonic() - t0, 1)
            row = {"model": spec_model, "verdict": verdict, "score": score, "evidence": str(evidence)[:200],
                   "diff_lines": diff.count("\n"), "wall_s": wall, "rc": rc}
            rows.append(row); fh.write(json.dumps(row) + "\n"); fh.flush()
            print(f"  {spec_model:<26} verdict={verdict} score={score} diff={diff.count(chr(10))}ln ({wall}s rc{rc})", flush=True)
            jj("workspace", "forget", name); sh("rm", "-rf", wt)
    print(f"\n{'model':<28}{'verdict':>14}{'score':>7}  (1=data_driven .5=partial 0=hardcoded)")
    for r in rows: print(f"{r['model']:<28}{r['verdict']:>14}{str(r['score']):>7}")

if __name__ == "__main__":
    sys.exit(main())
