#!/usr/bin/env python3
"""Panel/lens reducer — merge N review-runner result files into ONE review per
case and rescore, so fan-out configs (concern lenses, multi-model panels) are
comparable to a single-pass review on the same scorer.

Modes:
  union      every finding from every input (deduped) counts
  agree N    only findings matched across >= N distinct inputs count

Dedup/agreement match rule: same file (normalized, containment ok) AND
(identical kebab id OR token-Jaccard(id+summary) >= --jaccard). Dedup keeps the
highest-severity representative. Duplicate TRUE findings would otherwise score
as FPs (the scorer consumes one finding per expected).

Forbidden-claim caveat: rows don't carry raw text, so the merged 'forbidden'
count is approximated as min(sum(source rows' forbidden), len(case forbidden
claims)) — an upper bound; rare in practice.

usage:
  reduce_panel.py --cases cases/code-review/cases-final.json \
      --mode union results/review-*-lens-*.jsonl
  reduce_panel.py --mode agree --min-agree 2 A.jsonl B.jsonl C.jsonl
"""
import argparse, json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from review_runner import matches_expected, norm  # noqa: E402

RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}


def toks(f):
    return set(re.findall(r"[a-z0-9]+", norm(str(f.get("id", "")) + " " + str(f.get("summary", "")))))


def same_finding(a, b, jac):
    fa, fb = norm(a.get("file")), norm(b.get("file"))
    if not (fa == fb or fa in fb or fb in fa):
        return False
    if norm(a.get("id")) and norm(a.get("id")) == norm(b.get("id")):
        return True
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= jac


def merge(rows_by_src, mode, min_agree, jac):
    """rows_by_src: list of (src_label, findings). Returns merged findings list."""
    groups = []  # each: {"rep": finding, "srcs": set}
    for src, findings in rows_by_src:
        for f in findings:
            if not isinstance(f, dict):
                continue
            g = next((g for g in groups if same_finding(g["rep"], f, jac)), None)
            if g is None:
                groups.append({"rep": f, "srcs": {src}})
            else:
                g["srcs"].add(src)
                if RANK.get(norm(f.get("severity")), -1) > RANK.get(norm(g["rep"].get("severity")), -1):
                    g["rep"] = f
    if mode == "agree":
        groups = [g for g in groups if len(g["srcs"]) >= min_agree]
    return [g["rep"] for g in groups], groups


def score_merged(case, findings, forbidden_approx):
    expected = case.get("expected_findings", [])
    used = set()
    sev = 0.0
    for e in expected:
        idx = next((i for i, f in enumerate(findings) if i not in used and matches_expected(f, e)), None)
        if idx is None:
            continue
        used.add(idx)
        d = abs(RANK.get(norm(findings[idx].get("severity")), -10) - RANK.get(norm(e.get("severity")), -10))
        sev += 1.0 if d == 0 else 0.5 if d == 1 else 0.0
    matched, fp = len(used), len(findings) - len(used)
    forb = min(forbidden_approx, len(case.get("forbidden_claims", [])))
    recall = matched / max(1, len(expected))
    precision = matched / max(1, matched + fp)
    severity = sev / max(1, matched)
    return {"expected": len(expected), "matched": matched, "fp": fp, "forbidden": forb,
            "score": max(0.0, 0.50 * recall + 0.25 * precision + 0.25 * severity - min(0.25, 0.10 * forb))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--cases", default=str(HERE.parent / "cases/code-review/cases-final.json"))
    ap.add_argument("--mode", choices=("union", "agree"), default="union")
    ap.add_argument("--min-agree", type=int, default=2)
    ap.add_argument("--jaccard", type=float, default=0.35)
    ap.add_argument("--name", default=None, help="config name for the summary line")
    ap.add_argument("--out", default=None,
                    help="write per-case merged findings (JSONL: {case, findings, n_srcs}) for a downstream adjudicator")
    a = ap.parse_args()

    d = json.loads(pathlib.Path(a.cases).read_text())
    cases = {c["id"]: c for c in (d if isinstance(d, list) else d.get("cases", []))}

    per_case = {}  # case_id -> list[(src, findings)], forbidden_sum, parse_fails
    meta = {}
    for p in a.inputs:
        src = pathlib.Path(p).stem
        for line in pathlib.Path(p).read_text().splitlines():
            r = json.loads(line)
            cid = r["case"]
            e = per_case.setdefault(cid, {"srcs": [], "forbidden": 0, "parse_fail": 0})
            if r.get("parse_ok"):
                e["srcs"].append((src, r.get("findings", [])))
            else:
                e["parse_fail"] += 1
            e["forbidden"] += r.get("forbidden", 0)
    name = a.name or f"{a.mode}({len(a.inputs)} inputs)"
    tot, n = 0.0, 0
    trec_m, trec_e, tfp, tforb, tpf = 0, 0, 0, 0, 0
    outf = open(a.out, "w") if a.out else None
    for cid, e in per_case.items():
        case = cases.get(cid)
        if case is None:
            continue
        findings, groups = merge(e["srcs"], a.mode, a.min_agree, a.jaccard)
        if outf:
            outf.write(json.dumps({"case": cid, "forbidden": e["forbidden"],
                                   "findings": findings,
                                   "n_srcs": [len(g["srcs"]) for g in groups]}) + "\n")
        s = score_merged(case, findings, e["forbidden"])
        tot += s["score"]; n += 1
        trec_m += s["matched"]; trec_e += s["expected"]; tfp += s["fp"]
        tforb += s["forbidden"]; tpf += e["parse_fail"]
    if outf:
        outf.close()
    print(f"{name:<44} score={tot/max(1,n):.3f}  recall={trec_m}/{trec_e}"
          f"  fp={tfp}  forbidden~{tforb}  parse_fails={tpf}  cases={n}")


if __name__ == "__main__":
    main()
