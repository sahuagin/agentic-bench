#!/usr/bin/env python3
"""Multi-focus review panel: N reviewers, each ONE focus, spread across local
models (division of labor, cross-model). Tests whether the lens-panel UNION
raises catch-rate over a single generic reviewer on the real-PR cases — and at
what false-positive cost (the reduce step that prunes FPs comes next).

Model-major order (each model loaded ONCE, pre-warmed) to avoid VRAM thrash.
Empty/timeout from a pre-warm = LOAD failure, that model's lenses are skipped and
NOT scored (never score silence). Findings are written incrementally (resumable).
Reuses review_runner's hardened mu_ask + scorer. All via `mu ask --provider ollama`.
"""
import sys, json, pathlib, time, argparse, subprocess, os
from collections import defaultdict
HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); import review_runner as rr

LENSES = {
 "invariants":   "You review ONLY for violations of stated INVARIANTS and contracts — pre/postconditions, ordering guarantees ('X is written before Y'), state-machine rules, things that must always hold.",
 "architecture": "You review ONLY for ARCHITECTURE conformance — layering, module boundaries, the codebase's established patterns/abstractions, and whether the APPROACH (not just the lines) fits. The approach can be the bug.",
 "idiom":        "You review ONLY for non-IDIOMATIC Rust — error handling, ownership/borrowing, Option/Result/iterator misuse, naming, language-level smells.",
 "tests":        "You review ONLY for TEST adequacy — are the changed behaviors and their edge/error paths covered, and are the tests meaningful rather than tautological?",
 "delivered":    "You review ONLY for REQUEST-VS-DELIVERED — does the change actually do what it claims, and is anything MISSING (omissions, half-implemented surfaces, the complement of the diff)?",
 "edge":         "You review ONLY for unhandled EDGE CASES — boundaries, empty/None, concurrency/races, overflow, error propagation, resource cleanup.",
}
JSON_TAIL = (' Review ONLY the diff; do not claim to run tests or read other files. Return ONLY a JSON object: '
 '{"findings":[{"id":"kebab","severity":"blocker|major|minor|nit","file":"path","line":123,"summary":"...","rationale":"..."}]}. '
 'Empty findings list if nothing in YOUR focus is wrong.')

# lens -> local model (cross-model division of labor). Grouped model-major at run time.
ASSIGN = {
 "invariants":   "qwen3.6:35b-a3b-q8_0",
 "delivered":    "qwen3.6:35b-a3b-q8_0",
 "architecture": "ornith:35b",
 "edge":         "ornith:35b",
 "idiom":        "qwen3.6-code:latest",
 "tests":        "gpt-oss:20b",
}

def prewarm(model, t):
    import tempfile
    fd, of = tempfile.mkstemp(suffix=".out"); os.close(fd)
    cmd = ["timeout", "-k", "15", "-s", "TERM", str(t), "mu", "ask", "--bare",
           "--provider", "ollama", "--model", model, "Reply with exactly: ready"]
    try:
        with open(of, "w") as fh:
            subprocess.run(cmd, stdout=fh, stderr=subprocess.DEVNULL, timeout=t + 30)
        return bool(open(of, errors="replace").read().strip())
    except Exception:
        return False
    finally:
        try: os.unlink(of)
        except Exception: pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "cases/code-review/cases-final.json"))
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--load-timeout", type=int, default=300)
    ap.add_argument("--model", default="", help="run ALL lenses on this one model (e.g. ornith:9b) instead of the cross-model ASSIGN")
    ap.add_argument("--parallel", type=int, default=1, help="concurrent lens x case requests (needs OLLAMA_NUM_PARALLEL >= this); 1 = sequential")
    a = ap.parse_args()
    cases = [c for c in rr.load_cases([a.cases]) if c.get("provenance") == "reverse_fix"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "results" / f"lenspanel-{ts}.jsonl"; out.parent.mkdir(exist_ok=True)
    print(f"lens panel: {len(LENSES)} lenses x {len(cases)} real-PR cases -> {out.name}", flush=True)
    assign = {lens: a.model for lens in LENSES} if a.model else ASSIGN
    by_model = defaultdict(list)
    for lens, m in assign.items(): by_model[m].append(lens)
    findings = defaultdict(dict)  # case_id -> lens -> [findings]
    with out.open("w") as fh:
        for model, lenses in by_model.items():
            print(f"\n=== {model}  (lenses: {lenses}) ===", flush=True)
            if not prewarm(model, a.load_timeout):
                print(f"  LOAD_FAIL {model} — skipping its lenses (NOT scored)", flush=True)
                for lens in lenses:
                    fh.write(json.dumps({"model": model, "lens": lens, "load_fail": True}) + "\n"); fh.flush()
                continue
            tasks = [(lens, c) for lens in lenses for c in cases]
            def work(t):
                lens, c = t
                sysp = LENSES[lens] + JSON_TAIL
                prompt = f"{sysp}\n\n{c.get('instructions','Review this diff.')}\n\n{c['diff']}"
                raw, wall, err = rr.mu_ask("ollama", model, prompt, a.timeout)
                parsed = rr.parse_json(raw) if raw else None
                fl = (parsed.get("findings", []) if isinstance(parsed, dict) else [])
                return lens, c["id"], fl, parsed is not None, wall, err
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
                for lens, cid, fl, ok, wall, err in ex.map(work, tasks):
                    findings[cid][lens] = fl
                    fh.write(json.dumps({"model": model, "lens": lens, "case": cid,
                                         "findings": fl, "n_findings": len(fl), "parse_ok": ok,
                                         "wall_s": wall, "err": err}) + "\n"); fh.flush()
                    print(f"  {lens:<12} {cid:<42} n={len(fl)} {'ok' if ok else 'PARSE_FAIL'} ({wall}s)", flush=True)
    # score the lens-panel UNION per case
    agg = {"n": 0, "m": 0, "e": 0, "fp": 0, "score": 0.0}
    rows = []
    for c in cases:
        union = []
        for lens, fl in findings.get(c["id"], {}).items(): union.extend(fl)
        s = rr.score(c, {"findings": union}, "")
        rows.append({"case": c["id"], "union_n": len(union), "union_findings": union, "lenses": list(findings.get(c["id"], {}).keys()), **s})
        agg["n"] += 1; agg["m"] += s["matched"]; agg["e"] += s["expected"]; agg["fp"] += s["fp"]; agg["score"] += s["score"]
    (ROOT / "results" / f"lenspanel-{ts}-union.json").write_text(json.dumps(rows, indent=1))
    print(f"\n=== LENS-PANEL UNION (real-PR / reverse_fix) ===")
    print(f"recall={agg['m']/max(1,agg['e']):.2f}  fp={agg['fp']}  score={agg['score']/max(1,agg['n']):.3f}  over {agg['n']} cases")
    print("baseline single generic reviewer recall (review sweep, reverse_fix): "
          "gpt-5.5 0.88 / ornith 1.00 / qwen3.6:35b-a3b 0.94 / qwen3.6-code 0.88 / gpt-oss 0.88")
    print("NOTE: these single-bug cases ceiling-out on recall; the panel's real edge is the redo-bead corpus (thread 2).")

if __name__ == "__main__":
    sys.exit(main())
