#!/usr/bin/env python3
"""Verify a saved model diff ACTUALLY WORKS — beyond the invariant-lens's
"data_driven" direction grade. Applies the diff to a jj workspace at the mu base,
then `cargo check` (and optionally `cargo test`) the affected crate, reusing the
warm shared target so only the touched crate recompiles.

  ./cargo_verify_diff.py ~/cbench/hc_ollama_ornith_35b.diff [--test] [--crate mu-coding]

Answers, concretely: does the diff even apply? does it compile? do the tests pass?
A lens "data_driven" with a `cargo check` failure = right idea, didn't actually work.
"""
import sys, subprocess, os, argparse, pathlib, time

MU = "/home/tcovert/src/public_github/mu"
CBENCH = os.path.expanduser("~/cbench")

def sh(*a, cwd=None, timeout=1800, env=None, stdin=None):
    try:
        return subprocess.run(a, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env=env, input=stdin)
    except subprocess.TimeoutExpired:
        class R: returncode, stdout, stderr = 124, "", "timeout"
        return R()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("diff")
    ap.add_argument("--test", action="store_true", help="also run cargo test (slower)")
    ap.add_argument("--crate", default="mu-coding")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    diff = pathlib.Path(a.diff).read_text(errors="replace")
    if not diff.strip():
        print("EMPTY diff — nothing to verify"); return 1
    base = sh("git", "-C", MU, "rev-parse", "HEAD").stdout.strip()
    name = "verify_" + pathlib.Path(a.diff).stem.replace(".", "_")[:40]
    wt = f"{CBENCH}/{name}"
    sh("jj", "-R", MU, "workspace", "forget", name); sh("rm", "-rf", wt)
    add = sh("jj", "-R", MU, "workspace", "add", "--name", name, wt, "-r", base)
    if add.returncode != 0:
        print("WS_FAIL", add.stderr[:200]); return 1
    try:
        # apply — jj diff --git is git-format; try git apply then patch -p1
        ga = sh("git", "apply", "--3way", "--whitespace=nowarn", cwd=wt, stdin=diff)
        applied = ga.returncode == 0
        how = "git apply --3way"
        if not applied:
            pp = sh("patch", "-p1", "--forward", cwd=wt, stdin=diff)
            applied = pp.returncode == 0; how = "patch -p1"
            err = ga.stderr.strip() or pp.stderr.strip()
        print(f"APPLIES: {applied} ({how})" + ("" if applied else f"\n  ERR: {err[:400]}"))
        if not applied:
            print("  -> the diff is not a clean patch (likely truncated mid-hunk by the cut-off)")
            return 2
        env = dict(os.environ)
        if os.path.isdir(f"{CBENCH}/target-shared"):
            env["CARGO_TARGET_DIR"] = f"{CBENCH}/target-shared"
            print(f"  (warm target: {CBENCH}/target-shared)")
        else:
            print("  (NO warm target — first compile will be a full build)")
        os.makedirs(f"{CBENCH}/tmp", exist_ok=True); env["TMPDIR"] = f"{CBENCH}/tmp"
        t0 = time.monotonic()
        chk = sh("cargo", "check", "-p", a.crate, cwd=wt, timeout=a.timeout, env=env)
        print(f"COMPILES: {chk.returncode == 0}  (cargo check -p {a.crate}, {time.monotonic()-t0:.0f}s, rc={chk.returncode})")
        if chk.returncode != 0:
            errs = [l for l in chk.stderr.splitlines() if l.lstrip().startswith("error")][:20]
            print("  first errors:")
            for e in errs: print("   ", e.strip()[:200])
            return 3
        if a.test:
            t1 = time.monotonic()
            tst = sh("cargo", "test", "-p", a.crate, cwd=wt, timeout=a.timeout, env=env)
            res = [l for l in tst.stdout.splitlines() if "test result" in l]
            print(f"TESTS: rc={tst.returncode} ({time.monotonic()-t1:.0f}s)")
            for l in res: print("   ", l.strip())
            if tst.returncode != 0:
                fails = [l for l in tst.stdout.splitlines() if "FAILED" in l][:10]
                for f in fails: print("   ", f.strip()[:200])
    finally:
        sh("jj", "-R", MU, "workspace", "forget", name); sh("rm", "-rf", wt)
    return 0

if __name__ == "__main__":
    sys.exit(main())
