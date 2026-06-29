#!/usr/bin/env python3
"""Two-model review ensemble: a high-recall PROPOSER (e.g. ornith:35b) generates
findings, then a high-precision FILTER (e.g. gpt-oss:20b) keeps only the real
ones — testing whether the pair collapses false positives toward cloud quality
($0, both fit in 72 GB). Records `ornith-solo` and `ornith+gptoss-filter` on the
SAME run for an apples-to-apples comparison. Reuses review_runner's dispatch +
scorer (hardened mu_ask: shell timeout + file capture). All via `mu ask`.
"""
import sys, json, pathlib, time, argparse
from collections import defaultdict
HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); import review_runner as rr

FILTER_SYS = (
    "You are a precise code-review ADJUDICATOR. You are given a diff and a list of CANDIDATE "
    "findings produced by another reviewer (which is known to over-report). Return ONLY the "
    "candidates that are REAL issues actually present in the diff; drop false positives, "
    "speculation, and non-issues. Output ONLY {\"findings\":[...]} containing the kept finding "
    "objects verbatim. If none are real, return an empty list. Do NOT invent new findings, and "
    "do not claim to have run anything."
)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposer", default="ornith:35b")
    ap.add_argument("--filter", default="gpt-oss:20b")
    ap.add_argument("--cases", default=str(ROOT / "cases/code-review/cases-final.json"))
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    cases = rr.load_cases([a.cases])
    out = ROOT / "results" / f"ensemble-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(exist_ok=True)
    print(f"ensemble: proposer={a.proposer} -> filter={a.filter}  x {len(cases)} cases -> {out.name}", flush=True)
    with out.open("w") as fh:
        for c in cases:
            # stage 1 — proposer reviews the diff (blind: diff + instructions only)
            p1 = f"{rr.SYSTEM}\n\n{c.get('instructions','Review this diff.')}\n\n{c['diff']}"
            raw1, w1, _ = rr.mu_ask("ollama", a.proposer, p1, a.timeout)
            parsed1 = rr.parse_json(raw1) if raw1 else None
            s_solo = rr.score(c, parsed1, raw1)
            # stage 2 — filter keeps only the real findings
            if parsed1 is not None:
                cand = json.dumps(parsed1.get("findings", []))
                p2 = f"{FILTER_SYS}\n\nDIFF:\n{c['diff']}\n\nCANDIDATE FINDINGS:\n{cand}"
                raw2, w2, _ = rr.mu_ask("ollama", a.filter, p2, a.timeout)
                parsed2 = rr.parse_json(raw2) if raw2 else None
                s_ens = rr.score(c, parsed2, raw2) if parsed2 is not None else s_solo
            else:
                w2, s_ens = 0, s_solo  # proposer unparseable -> nothing to filter
            for variant, s, w in (("ornith-solo", s_solo, w1),
                                   ("ornith+gptoss-filter", s_ens, w1 + (w2 or 0))):
                fh.write(json.dumps({"model": variant, "case": c['id'],
                                     "provenance": c.get('provenance'), "wall_s": w, **s}) + "\n")
                fh.flush()
            print(f"  {c['id']:<42} solo={s_solo['score']:.2f}(fp{s_solo['fp']}) -> ens={s_ens['score']:.2f}(fp{s_ens['fp']})", flush=True)
    # summary (real-PR / reverse_fix subset, parseable only)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    agg = defaultdict(lambda: [0, 0.0, 0, 0, 0])
    for r in rows:
        if r.get('provenance') != 'reverse_fix' or not r.get('parse_ok'):
            continue
        x = agg[r['model']]; x[0]+=1; x[1]+=r['score']; x[2]+=r['matched']; x[3]+=r['expected']; x[4]+=r['fp']
    print(f"\n{'variant':<24}{'real-PR':>8}{'recall':>8}{'fp':>5}")
    for m, x in agg.items():
        n, ss, rm, re_, fp = x
        print(f"{m:<24}{ss/max(1,n):>8.3f}{rm/max(1,re_):>8.2f}{fp:>5}")

if __name__ == "__main__":
    sys.exit(main())
