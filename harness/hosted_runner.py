#!/usr/bin/env python3
"""Route code-review-bench cases through HOSTED models, reusing run_benchmark's
prompt + scorer so records stay byte-compatible with summarize.py.

Backends:
  - mu ask --bare --provider {openai-codex,openrouter} --model ...
  - claude -p (the Claude Code subscription) for claude-* models

Secrets: OPENROUTER_API_KEY is read from the inherited env and passed through to
the spawned `mu serve` by inheritance only. This script never reads, prints, or
logs the key.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import run_benchmark as rb  # reuse SYSTEM_PROMPT, make_prompt, extract_json, score, load_cases, ROOT

# The ~/.local/bin/mu symlink is the emu build-on-launch wrapper (recompiles
# every invocation). Use the static release binary so a 30-call run doesn't
# rebuild-check per call; `mu ask` spawns `mu serve` via its own exe, keeping
# both sides on this binary. Override with MU_BIN if needed.
MU = os.environ.get("MU_BIN", os.path.expanduser("~/src/public_github/mu/target/release/mu"))
CLAUDE = os.path.expanduser("~/.local/bin/claude")

# The run set lives in bench-hosted/models.toml (sibling of scripts/) so it's
# config, not code — edit there, no code change, to add/drop models. Override
# the path with $BENCH_MODELS. Each entry -> (backend, provider|None, model):
#   backend "mu"     -> `mu ask --provider <provider> --model <model>`
#   backend "claude" -> `claude -p` (subscription); provider is None
# Local ollama models run at production 262k ctx, serialized one-at-a-time on the
# shared GPU (ollama stop between — see --ollama-host).
import tomllib

MODELS_TOML = os.environ.get(
    "BENCH_MODELS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models.toml"),
)


def _load_models(path):
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)

    def rows(key):
        return [(m.get("backend", "mu"), (m.get("provider") or None), m["model"])
                for m in cfg.get(key, [])]

    return rows("hosted"), rows("local")


HOSTED_MODELS, LOCAL_MODELS = _load_models(MODELS_TOML)


def _write_tmp(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def run_mu(provider: str, model: str, sys_prompt: str, user_prompt: str, timeout: int):
    sysf = _write_tmp(sys_prompt, ".sys")
    usrf = _write_tmp(user_prompt, ".usr")
    cmd = [MU, "ask", "--bare", "--provider", provider, "--model", model,
           "--append-system-prompt", sysf, "--prompt-file", usrf]
    # start_new_session: put `mu ask` in its own process group so a timeout can
    # kill the WHOLE group (incl. any `mu serve` it spawns). Without this, an
    # orphaned `mu serve` keeps the stdout pipe open and communicate() deadlocks
    # forever past the timeout — the wedge that silently hung the run for ~1h.
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, errtext = p.communicate(timeout=timeout)
        raw = (out or "").strip()
        err = None if (p.returncode == 0 and raw) else ((errtext or "").strip()[:600] or f"exit={p.returncode}")
    except subprocess.TimeoutExpired:
        import signal
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            p.communicate(timeout=15)
        except Exception:
            pass
        raw, err = "", "timeout"
    finally:
        os.unlink(sysf); os.unlink(usrf)
    return raw, err


def run_claude(model: str, sys_prompt: str, user_prompt: str, timeout: int):
    sysf = _write_tmp(sys_prompt, ".sys")
    # Run from an empty cwd so a stray Read/Grep finds nothing; strip the agentic
    # dynamic system-prompt sections; force a bare text completion.
    workdir = tempfile.mkdtemp(prefix="bench-claude-")
    cmd = [CLAUDE, "-p", "--model", model,
           "--system-prompt-file", sysf,
           "--exclude-dynamic-system-prompt-sections",
           "--output-format", "text", user_prompt]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
        raw = (p.stdout or "").strip()
        err = None if (p.returncode == 0 and raw) else ((p.stderr or "").strip()[:600] or f"exit={p.returncode}")
    except subprocess.TimeoutExpired:
        raw, err = "", "timeout"
    finally:
        os.unlink(sysf)
        try:
            os.rmdir(workdir)
        except OSError:
            pass
    return raw, err


def ollama_stop(host: str, model: str):
    subprocess.run(["ssh", host, "ollama", "stop", model],
                   capture_output=True, text=True, timeout=60)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", choices=("hosted", "local", "all"), default="hosted")
    ap.add_argument("--limit-cases", type=int)
    ap.add_argument("--only", nargs="*", help="substring match on model id to restrict the run")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tag")
    ap.add_argument("--ollama-host", default="127.0.0.1",
                    help="ssh target for `ollama stop` between local (serialized) models")
    args = ap.parse_args()

    pool = {"hosted": HOSTED_MODELS, "local": LOCAL_MODELS,
            "all": HOSTED_MODELS + LOCAL_MODELS}[args.suite]
    models = pool
    if args.only:
        models = [m for m in pool if any(s in m[2] for s in args.only)]
        if not models:
            ap.error("no models matched --only")

    tag = args.tag or args.suite
    cases = rb.load_cases(args.limit_cases)
    out_dir = rb.ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"run-{stamp}-{tag}.jsonl"

    with out_path.open("w") as fh:
        for backend, provider, model in models:
            for case in cases:
                print(f"{model} :: {case['id']}", file=sys.stderr, flush=True)
                t0 = time.time()
                sysp, usrp = rb.SYSTEM_PROMPT, rb.make_prompt(case)
                if backend == "mu":
                    raw, err = run_mu(provider, model, sysp, usrp, args.timeout)
                else:
                    raw, err = run_claude(model, sysp, usrp, args.timeout)
                elapsed = time.time() - t0
                parsed, perr = rb.extract_json(raw) if raw else (None, err or "empty response")
                scored = rb.score(case, parsed, raw, perr)
                rec = {
                    "run_at": datetime.now(timezone.utc).isoformat(),
                    "model": model,
                    "case_id": case["id"],
                    "provider": provider or "claude-sub",
                    "num_ctx": None,
                    "num_predict": None,
                    "temperature": 0.0,
                    "elapsed_s": elapsed,
                    "error": err,
                    "raw_response": raw,
                    "parsed": parsed,
                    "score": scored,
                    "ollama_metrics": {},
                }
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
            # Serialize the shared GPU: unload this local model before the next.
            if provider == "ollama":
                print(f"{model} :: ollama stop", file=sys.stderr, flush=True)
                ollama_stop(args.ollama_host, model)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
