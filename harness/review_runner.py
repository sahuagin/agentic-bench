#!/usr/bin/env python3
"""Code-review benchmark runner — real-PR cases through the production `mu ask`
path, across lanes. Scores recall/precision/severity + claim-discipline, and
records parse_ok per result (the trust guard: a hallucinating/empty model shows
as low parse-rate, never a clean fake average).

Lanes (provider passed to `mu ask`):
  ollama     -> --provider ollama       (local .143 box; model e.g. qwen3.6-code:latest)
  codex      -> --provider openai-codex (gpt-5.5, OpenAI subscription)
  openrouter -> --provider openrouter   (open models, metered)
Anthropic (opus/sonnet) is NOT here — it runs via cc subagents on the subscription.

Models are given as `lane:model`, e.g.
  ./review_runner.py --models ollama:qwen3.6-code:latest codex:gpt-5.5 openrouter:deepseek/deepseek-v4-pro
Cases default to cases/code-review/cases-final.json (+ any extra --cases files merged).
No hardcoded hosts/paths: `mu ask` resolves the provider endpoint from mu's own config.
"""
import argparse, json, pathlib, re, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
LANES = {"ollama": "ollama", "codex": "openai-codex", "openrouter": "openrouter"}

SYSTEM = (
    "You are a strict code reviewer. Review ONLY the diff and instructions provided. "
    "Do NOT claim you ran tests, opened files, or verified behavior outside the diff. "
    'Return ONLY a JSON object: {"findings":[{"id":"kebab","severity":"blocker|major|minor|nit",'
    '"file":"path","line":int,"summary":"...","rationale":"..."}],"overall":"approve|request_changes",'
    '"notes":"..."}. If the diff is correct, return an empty findings list.'
)

# ---- scorer (verbatim from the code-review-bench harness) -------------------
def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", str(s or "").lower())).strip()
def finding_text(f):
    return norm(" ".join(str(f.get(k, "")) for k in ("id", "severity", "file", "summary", "rationale")))
def matches_expected(f, e):
    file_ok = norm(f.get("file")) == norm(e.get("file")) or norm(e.get("file")) in norm(f.get("file"))
    text = finding_text(f); hits = 0
    for k in (norm(x) for x in e.get("keywords", [])):
        if k in text: hits += 1
        else:
            parts = [p for p in k.split() if p]
            if len(parts) > 1 and all(p in text for p in parts): hits += 1
    return (file_ok and hits >= 2) or hits >= 4
def score(case, parsed, raw):
    expected = case.get("expected_findings", [])
    out = {"parse_ok": parsed is not None, "expected": len(expected), "matched": 0,
           "fp": 0, "forbidden": 0, "severity_points": 0.0, "score": 0.0}
    if parsed is None:
        return out
    findings = parsed.get("findings", [])
    findings = findings if isinstance(findings, list) else []
    used, rank = set(), {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
    for e in expected:
        idx = next((i for i, f in enumerate(findings)
                    if i not in used and isinstance(f, dict) and matches_expected(f, e)), None)
        if idx is None: continue
        used.add(idx); f = findings[idx]
        delta = abs(rank.get(norm(f.get("severity")), -10) - rank.get(norm(e.get("severity")), -10))
        out["severity_points"] += 1.0 if delta == 0 else 0.5 if delta == 1 else 0.0
    out["matched"] = len(used)
    out["fp"] = len([1 for i in range(len(findings)) if i not in used])
    raw_l = raw.lower()
    out["forbidden"] = sum(1 for c in case.get("forbidden_claims", []) if c.lower() in raw_l)
    recall = out["matched"] / max(1, len(expected))
    precision = out["matched"] / max(1, out["matched"] + out["fp"])
    severity = out["severity_points"] / max(1, out["matched"])
    out["score"] = max(0.0, 0.50 * recall + 0.25 * precision + 0.25 * severity
                       - min(0.25, 0.10 * out["forbidden"]))
    return out

# ---- dispatch + parse ------------------------------------------------------
def mu_ask(provider, model, prompt, timeout):
    # Hard shell `timeout` + stdout->FILE (not a pipe): `mu ask` spawns a
    # `mu serve` child that inherits the pipe and keeps it open, which hangs
    # subprocess's own timeout/communicate. A file fd + `timeout -k` avoids both.
    import tempfile, os
    t0 = time.monotonic()
    fd, out = tempfile.mkstemp(suffix=".out"); os.close(fd)
    cmd = ["timeout", "-k", "10", "-s", "TERM", str(timeout),
           "mu", "ask", "--bare", "--provider", provider, "--model", model, prompt]
    try:
        with open(out, "w") as fh:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.DEVNULL, timeout=timeout + 30)
        text = open(out, errors="replace").read().strip()
        err = None if r.returncode == 0 else ("timeout" if r.returncode == 124 else f"exit{r.returncode}")
        return text, round(time.monotonic() - t0, 1), err
    except subprocess.TimeoutExpired:
        return "", round(time.monotonic() - t0, 1), "timeout-hard"
    except Exception as e:  # noqa: BLE001
        return "", round(time.monotonic() - t0, 1), repr(e)
    finally:
        try: os.unlink(out)
        except Exception: pass

def parse_json(text):
    for attempt in (text, ):
        try: return json.loads(attempt)
        except Exception: pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except Exception: return None
    return None

def load_cases(paths):
    cases = []
    for p in paths:
        d = json.loads(pathlib.Path(p).read_text())
        cases.extend(d if isinstance(d, list) else d.get("cases", []))
    return cases

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True, help="lane:model (ollama|codex|openrouter)")
    ap.add_argument("--cases", nargs="+", default=[str(ROOT / "cases/code-review/cases-final.json")])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    cases = load_cases(a.cases)
    out_path = ROOT / "results" / f"review-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    print(f"review-bench: {len(a.models)} models x {len(cases)} cases x {a.reps} reps -> {out_path.name}", flush=True)
    with out_path.open("w") as fh:
        for spec in a.models:
            lane, _, model = spec.partition(":")
            provider = LANES.get(lane)
            if not provider:
                print(f"  !! unknown lane {lane!r} in {spec}; skipping", flush=True); continue
            print(f"\n=== {spec} (provider={provider}) ===", flush=True)
            for case in cases:
                for rep in range(1, a.reps + 1):
                    prompt = f"{SYSTEM}\n\n{case.get('instructions','Review this diff.')}\n\n{case['diff']}"
                    raw, wall, err = mu_ask(provider, model, prompt, a.timeout)
                    parsed = parse_json(raw) if raw else None
                    s = score(case, parsed, raw)
                    row = {"model": spec, "provider": provider, "case": case["id"],
                           "provenance": case.get("provenance"), "rep": rep, "wall_s": wall,
                           "error": err, **s}
                    fh.write(json.dumps(row) + "\n"); fh.flush()
                    mark = "ok" if s["parse_ok"] else ("ERR" if err else "PARSE_FAIL")
                    print(f"  {case['id']:<34} rep{rep} score={s['score']:.2f} "
                          f"r={s['matched']}/{s['expected']} {mark} ({wall}s)", flush=True)
    summarize(out_path)

def summarize(path):
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    print(f"\n{'model':<40}{'score':>7}{'parse%':>8}{'recall':>8}{'fp':>5}")
    for m in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == m]
        parse_rate = 100 * sum(r["parse_ok"] for r in mine) / len(mine)
        # trust guard: scores only meaningful over parseable runs
        ok = [r for r in mine if r["parse_ok"]]
        sc = sum(r["score"] for r in ok) / len(ok) if ok else 0.0
        rec = sum(r["matched"] for r in ok) / max(1, sum(r["expected"] for r in ok))
        fp = sum(r["fp"] for r in ok)
        flag = "  <-- LOW PARSE, distrust" if parse_rate < 80 else ""
        print(f"{m:<40}{sc:>7.3f}{parse_rate:>7.0f}%{rec:>8.2f}{fp:>5}{flag}")

if __name__ == "__main__":
    sys.exit(main())
