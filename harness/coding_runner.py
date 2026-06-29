#!/usr/bin/env python3
"""Coding-worker bench: the model gets a leak-free task spec + its OWN clean jj
workspace at the buggy base commit, EDITS it (read/edit/grep/glob tools), and we
score its diff against a HIDDEN cargo acceptance test (1 pass / 0.5 compiles-but-
fails / 0 no-compile or no-fix). Same lanes as review_runner (ollama / codex /
openrouter via `mu ask`; anthropic runs via cc subagents, not here).

Each model works in its own `jj workspace add` checkout — isolated, trivial to
forget, and the main repo's working copy is never touched. The hidden test is
injected into the worker's workspace and built with a per-case shared
CARGO_TARGET_DIR (cold once, incremental after).

  ./coding_runner.py --models ollama:qwen3.6:35b-a3b-q8_0 codex:gpt-5.5 --cases effort
"""
import sys, json, subprocess, pathlib, time, argparse, os

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
CODING = ROOT / "cases/coding"
# Path to the mu checkout the coding cases build against. Override with MU_REPO;
# defaults to a conventional location (no hardcoded home dir -> public-gate clean).
MU = os.environ.get("MU_REPO") or os.path.expanduser("~/src/public_github/mu")
# Build OFF /tmp: this host's /tmp is a restricted tmpfs that breaks clang's
# temp-file creation for ring's asm build. Workspaces + CARGO_TARGET_DIR live
# under $HOME (a normal FS, where mu builds fine).
CBENCH = os.path.expanduser("~/cbench")
WORKER_SYS = (CODING / "worker-sys.md").read_text()
LANES = {"ollama": "ollama", "codex": "openai-codex", "openrouter": "openrouter"}

EFFORT_TEST = '''
    #[test]
    fn bench_acceptance_openai_api_effort() {
        let (levels, default) = super::effort_config("openai_api", &ResolvedModelSettings::default());
        let levels: Vec<String> = levels.expect("openai_api must have a fallback").iter().map(|s| s.to_string()).collect();
        assert_eq!(levels, vec!["low", "medium", "high", "xhigh"]);
        assert!(!levels.iter().any(|l| l == "minimal"));
        assert_eq!(default.as_deref(), Some("medium"));
    }
'''

CASES = {
    "effort": {"spec": CODING / "spec-effort.md", "base": "1a1b184", "crate": "mu-core",
               "test": "bench_acceptance_openai_api_effort",
               "path": "crates/mu-core/src/route_catalog.rs",
               "marker": "mod vcbm_effort_tests {", "inject": EFFORT_TEST},
    "orphan": {"spec": CODING / "orphan/spec-orphan.md", "base": "96dafb2", "crate": "mu-core",
               "test": "bench_acceptance_no_orphan_on_parallel_compaction",
               "path": "crates/mu-core/src/context/compaction/heuristic.rs",
               "marker": "mod tests {",
               "inject": (CODING / "orphan/orphan-test.txt").read_text() if (CODING / "orphan/orphan-test.txt").exists() else ""},
}

def sh(*a, cwd=None, timeout=120, env=None):
    try:
        return subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        class R: returncode, stdout, stderr = 124, "", "timeout"
        return R()

def jj(*a, timeout=120):
    return sh("jj", "-R", MU, *a, timeout=timeout)

def dispatch(lane, model, prompt, wt, timeout):
    import tempfile
    fd, of = tempfile.mkstemp(suffix=".out"); os.close(fd)
    cmd = ["timeout", "-k", "20", "-s", "TERM", str(timeout), "mu", "ask", "--bare",
           "--provider", LANES[lane], "--model", model, "--tools", "read,edit,grep,glob", prompt]
    try:
        with open(of, "w") as fh:
            r = subprocess.run(cmd, cwd=wt, stdout=fh, stderr=subprocess.DEVNULL, timeout=timeout + 40)
        return r.returncode
    except subprocess.TimeoutExpired:
        return 124
    finally:
        try: os.unlink(of)
        except Exception: pass

def cargo_score(wt, cs, c, timeout):
    # inject the hidden test into the worker's workspace, then build+test
    p = os.path.join(wt, c["path"])
    s = open(p).read()
    if c["test"] not in s and c["inject"]:
        i = s.index(c["marker"]) + len(c["marker"])
        open(p, "w").write(s[:i] + "\n" + c["inject"] + s[i:])
    env = dict(os.environ, CARGO_TARGET_DIR=f"{CBENCH}/target-shared", TMPDIR=f"{CBENCH}/tmp")
    r = sh("cargo", "test", "-p", c["crate"], c["test"], cwd=wt, timeout=timeout, env=env)
    so = r.stdout or ""
    if "test result: ok" in so:
        return 1.0, "pass"
    if r.returncode == 124:
        return 0.0, "timeout"
    if "test result: FAILED" in so:
        return 0.5, "test_fail"   # compiled + the test RAN, assertion failed
    return 0.0, "build_fail"      # never built/ran: rustc error[, cc-rs/build-script, link, etc.

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True, help="lane:model")
    ap.add_argument("--cases", nargs="+", default=list(CASES))
    ap.add_argument("--worker-timeout", type=int, default=600)
    ap.add_argument("--score-timeout", type=int, default=1500)
    a = ap.parse_args()
    os.makedirs(f"{CBENCH}/tmp", exist_ok=True)  # native temp dir (off restricted /tmp)
    if not os.path.isdir(f"{CBENCH}/target-shared"):
        print(f"!! {CBENCH}/target-shared missing — copy the prebuilt target first:\n"
              f"   cp -a {MU}/target {CBENCH}/target-shared", flush=True)
        return 2
    out = ROOT / "results" / f"coding-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(exist_ok=True)
    print(f"coding: {a.models} x {a.cases} -> {out.name}", flush=True)
    with out.open("w") as fh:
        for cs in a.cases:
            c = CASES[cs]; spec = pathlib.Path(c["spec"]).read_text()
            for spec_model in a.models:
                lane, _, model = spec_model.partition(":")
                if lane not in LANES:
                    print(f"  !! unknown lane {lane!r}; skip", flush=True); continue
                slug = f"{cs}-{model}".replace(":", "_").replace("/", "_").replace(".", "")
                name = f"cb_{slug}"; wt = f"{CBENCH}/{name}"
                jj("workspace", "forget", name); sh("rm", "-rf", wt)
                add = jj("workspace", "add", "--name", name, wt, "-r", c["base"])
                if add.returncode != 0:
                    print(f"  {cs:<7} {spec_model:<26} WS_FAIL {add.stderr.strip()[:90]}", flush=True); continue
                t0 = time.monotonic()
                rc = dispatch(lane, model, WORKER_SYS + "\n\n# TASK\n" + spec, wt, a.worker_timeout)
                diff = sh("jj", "diff", "--git", cwd=wt).stdout
                open(f"{CBENCH}/{slug}.diff", "w").write(diff)
                score, reason = cargo_score(wt, cs, c, a.score_timeout)
                wall = round(time.monotonic() - t0, 1)
                row = {"model": spec_model, "case": cs, "score": score, "reason": reason,
                       "diff_lines": diff.count("\n"), "dispatch_rc": rc, "wall_s": wall}
                fh.write(json.dumps(row) + "\n"); fh.flush()
                print(f"  {cs:<7} {spec_model:<26} SCORE={score} ({reason}) diff={diff.count(chr(10))}ln {wall}s rc{rc}", flush=True)
                jj("workspace", "forget", name); sh("rm", "-rf", wt)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    from collections import defaultdict
    agg = defaultdict(list)
    for r in rows: agg[r["model"]].append((r["case"], r["score"]))
    print(f"\n{'model':<28}{'avg':>6}  per-case")
    for m, cs in agg.items():
        print(f"{m:<28}{sum(s for _, s in cs)/max(1,len(cs)):>6.2f}  {dict(cs)}")

if __name__ == "__main__":
    sys.exit(main())
