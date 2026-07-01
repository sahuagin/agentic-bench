#!/usr/bin/env python3
"""Stage-2 adjudicator — the precision half of a two-stage review.

Stage 1 (recall): cheap local fan-out (concern lenses / panel) unions into
candidate findings per case (reduce_panel.py --out). Stage 2 (this): a strong
model re-reads the DIFF and each candidate, keeps REAL, kills BOGUS. The kept
set is rescored on the same scorer, so 'lens-union + adjudicator' is directly
comparable to single-pass and raw-union rows.

usage:
  adjudicate_findings.py --candidates union.jsonl [--lane codex --model gpt-5.5]
"""
import argparse, json, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from review_runner import mu_ask, claude_ask, parse_json, LANES  # noqa: E402
from reduce_panel import score_merged  # noqa: E402

SYSTEM = (
    "You are a strict review adjudicator. You get a DIFF and NUMBERED candidate findings "
    "from earlier reviewers. Judge each candidate ONLY against the diff: REAL means the issue "
    "is genuinely present in the changed code and worth reporting; BOGUS means it is wrong, "
    "speculative, out of scope for this diff, duplicated, or style-only noise. Do NOT invent "
    "new findings. Return ONLY JSON: {\"verdicts\":[{\"i\":<candidate number>,\"verdict\":\"REAL|BOGUS\","
    "\"reason\":\"<one line>\"}]} with exactly one verdict per candidate."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="reduce_panel.py --out file")
    ap.add_argument("--cases", default=str(HERE.parent / "cases/code-review/cases-final.json"))
    ap.add_argument("--lane", default="codex")
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--name", default=None)
    a = ap.parse_args()

    d = json.loads(pathlib.Path(a.cases).read_text())
    cases = {c["id"]: c for c in (d if isinstance(d, list) else d.get("cases", []))}
    provider = LANES[a.lane]

    out_path = HERE.parent / "results" / f"adjudicated-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    tot = n = 0
    trec_m = trec_e = tfp = tforb = 0
    with out_path.open("w") as fh:
        for line in pathlib.Path(a.candidates).read_text().splitlines():
            row = json.loads(line)
            case = cases.get(row["case"])
            if case is None:
                continue
            cands = row["findings"]
            if cands:
                numbered = "\n".join(
                    f"[{i}] file={f.get('file')} line={f.get('line')} severity={f.get('severity')} "
                    f"summary={f.get('summary')} rationale={f.get('rationale','')[:300]}"
                    for i, f in enumerate(cands))
                prompt = (f"{SYSTEM}\n\nDIFF:\n{case['diff']}\n\n"
                          f"CANDIDATE FINDINGS ({len(cands)}):\n{numbered}")
                if a.lane == "claude":
                    raw, wall, err = claude_ask(a.model, prompt, a.timeout)
                else:
                    raw, wall, err = mu_ask(provider, a.model, prompt, a.timeout)
                parsed = parse_json(raw) if raw else None
                verdicts = (parsed or {}).get("verdicts", [])
                real_idx = {v.get("i") for v in verdicts
                            if isinstance(v, dict) and str(v.get("verdict", "")).upper() == "REAL"}
                # fail-open per candidate: no parseable verdict for i -> keep it
                # (an adjudicator outage must not silently drop real findings)
                judged = {v.get("i") for v in verdicts if isinstance(v, dict)}
                kept = [f for i, f in enumerate(cands) if i in real_idx or i not in judged]
                adjudicated = bool(parsed)
            else:
                kept, wall, err, adjudicated = [], 0.0, None, True
            s = score_merged(case, kept, row.get("forbidden", 0))
            tot += s["score"]; n += 1
            trec_m += s["matched"]; trec_e += s["expected"]; tfp += s["fp"]; tforb += s["forbidden"]
            fh.write(json.dumps({"case": row["case"], "kept": kept, "n_cand": len(cands),
                                 "adjudicated": adjudicated, "error": err, **s}) + "\n")
            print(f"  {row['case']:<38} cand={len(cands):>2} kept={len(kept):>2} "
                  f"score={s['score']:.2f} r={s['matched']}/{s['expected']} ({wall}s)"
                  f"{'' if adjudicated else '  [NO-VERDICT, fail-open]'}", flush=True)
    name = a.name or f"adjudicated({a.lane}:{a.model})"
    print(f"\n{name:<44} score={tot/max(1,n):.3f}  recall={trec_m}/{trec_e}"
          f"  fp={tfp}  forbidden~{tforb}  cases={n}  -> {out_path.name}")


if __name__ == "__main__":
    main()
