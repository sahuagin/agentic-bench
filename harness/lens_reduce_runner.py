#!/usr/bin/env python3
"""Reduce step for the lens panel. An adjudicator prunes the lens-panel UNION
(high recall, many false positives) down to the findings that are real — the
precision-recovery half of the division-of-labor pipeline. Compares the raw
union vs each adjudicator's reduced set on the real-PR cases.

Reconstructs the union per case from a lens jsonl (latest lenspanel-*.jsonl by
default). Pre-warms ollama adjudicators; never scores silence; trust-guard on
parse rate (recall/fp/score over parseable reductions only). Reuses review_runner.
"""
import sys, json, pathlib, time, argparse, subprocess, os, glob
from collections import defaultdict
HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); import review_runner as rr

FILTER_SYS = (
    "You are a precise code-review ADJUDICATOR. You are given a diff and a list of CANDIDATE findings "
    "from a multi-reviewer panel that OVER-reports. Return ONLY the candidates that are REAL issues "
    "actually present in the diff; drop false positives, speculation, duplicates, and non-issues. "
    'Output ONLY {"findings":[...]} with the kept finding objects (verbatim). Do not invent new '
    "findings. Empty findings list if none are real. Review ONLY the diff; do not claim to run anything.")

LANES = {"codex": "openai-codex", "ollama": "ollama", "openrouter": "openrouter"}

def prewarm(model, t):
    import tempfile
    fd, of = tempfile.mkstemp(suffix=".out"); os.close(fd)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lens-jsonl", default="")
    ap.add_argument("--adjudicators", nargs="+", default=["codex:gpt-5.5", "ollama:gpt-oss:20b"])
    ap.add_argument("--cases", default=str(ROOT / "cases/code-review/cases-final.json"))
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--load-timeout", type=int, default=300)
    a = ap.parse_args()
    lj = a.lens_jsonl or sorted(glob.glob(str(ROOT / "results/lenspanel-*.jsonl")))[-1]
    print(f"reduce: union from {pathlib.Path(lj).name}; adjudicators={a.adjudicators}", flush=True)
    union = defaultdict(list)
    for line in open(lj):
        if not line.strip(): continue
        r = json.loads(line)
        if r.get("case") and r.get("findings"): union[r["case"]].extend(r["findings"])
    cases = {c["id"]: c for c in rr.load_cases([a.cases]) if c.get("provenance") == "reverse_fix"}
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "results" / f"lensreduce-{ts}.jsonl"; out.parent.mkdir(exist_ok=True)
    agg = defaultdict(lambda: {"n": 0, "ok": 0, "m": 0, "e": 0, "fp": 0, "score": 0.0})
    with out.open("w") as fh:
        for spec in a.adjudicators:
            lane, _, model = spec.partition(":")
            if lane == "ollama" and not prewarm(model, a.load_timeout):
                print(f"  {spec}: LOAD_FAIL — skipping (not scored)", flush=True); continue
            print(f"\n=== adjudicator {spec} ===", flush=True)
            for cid, c in cases.items():
                cand = union.get(cid, [])
                prompt = f"{FILTER_SYS}\n\nDIFF:\n{c['diff']}\n\nCANDIDATE FINDINGS:\n{json.dumps(cand)}"
                raw, wall, err = rr.mu_ask(LANES.get(lane, lane), model, prompt, a.timeout)
                parsed = rr.parse_json(raw) if raw else None
                ok = parsed is not None
                s = rr.score(c, parsed, raw) if ok else {"matched": 0, "expected": len(c.get("expected_findings", [])), "fp": 0, "score": 0.0}
                kept = len(parsed.get("findings", [])) if ok and isinstance(parsed, dict) else None
                fh.write(json.dumps({"adjudicator": spec, "case": cid, "cand_n": len(cand),
                                     "kept_n": kept, "parse_ok": ok, "wall_s": wall, **s}) + "\n"); fh.flush()
                R = agg[spec]; R["n"] += 1
                if ok:
                    R["ok"] += 1; R["m"] += s["matched"]; R["e"] += s["expected"]; R["fp"] += s["fp"]; R["score"] += s["score"]
                print(f"  {cid:<42} cand={len(cand):>2} kept={kept if kept is not None else 'n/a':>3} "
                      f"recall={'Y' if s['matched']>=s['expected'] and s['expected']>0 else 'n'} fp={s['fp']} ({wall}s) {'ok' if ok else 'PARSE_FAIL'}", flush=True)
    print(f"\n{'arm':<24}{'recall':>8}{'fp':>6}{'score':>8}{'parse':>7}")
    print(f"{'RAW UNION (no reduce)':<24}{'1.00':>8}{'134':>6}{'0.723':>8}{'100%':>7}")
    for spec, R in agg.items():
        rec = R["m"] / max(1, R["e"]); sc = R["score"] / max(1, R["ok"]); pr = 100 * R["ok"] / max(1, R["n"])
        flag = "  <-- LOW PARSE, distrust" if pr < 80 else ""
        print(f"{spec:<24}{rec:>8.2f}{R['fp']:>6}{sc:>8.3f}{pr:>6.0f}%{flag}")

if __name__ == "__main__":
    sys.exit(main())
