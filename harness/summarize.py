#!/usr/bin/env python3
"""Summarize benchmark JSONL into console tables and a static HTML report."""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def ns_to_s(value: Any) -> float:
    return (value or 0) / 1_000_000_000


def read_records(paths: list[str]) -> list[dict[str, Any]]:
    records = []
    for pat in paths:
        for path in sorted(Path().glob(pat) if any(c in pat for c in "*?[") else [Path(pat)]):
            if not path.exists():
                continue
            with path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scoring_records = [r for r in records if r.get("record_type") != "warmup"]
    warmups = {r["model"]: r for r in records if r.get("record_type") == "warmup"}
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scoring_records:
        by_model[r["model"]].append(r)
    rows = []
    for model, recs in by_model.items():
        scores = [r["score"]["score"] for r in recs]
        matched = sum(r["score"]["matched_count"] for r in recs)
        expected = sum(r["score"]["expected_count"] for r in recs)
        fp = sum(r["score"]["false_positive_count"] for r in recs)
        forbidden = sum(r["score"].get("forbidden_claim_count", 0) for r in recs)
        parse_ok = sum(1 for r in recs if r["score"]["parse_ok"])
        elapsed = [r.get("elapsed_s", 0.0) for r in recs]
        metrics = [r.get("ollama_metrics") or {} for r in recs]
        eval_toks = sum(m.get("eval_count") or 0 for m in metrics)
        eval_ns = sum(m.get("eval_duration") or 0 for m in metrics)
        prompt_ns = [ns_to_s(m.get("prompt_eval_duration")) for m in metrics]
        load_ns = [ns_to_s(m.get("load_duration")) for m in metrics]
        total_ns = [ns_to_s(m.get("total_duration")) for m in metrics]
        warmup = warmups.get(model, {})
        warm_metrics = warmup.get("ollama_metrics") or {}
        rows.append({
            "model": model,
            "cases": len(recs),
            "num_ctx": recs[0].get("num_ctx"),
            "avg_score": mean(scores) if scores else 0.0,
            "recall": matched / max(1, expected),
            "precision": matched / max(1, matched + fp),
            "matched": matched,
            "expected": expected,
            "false_positives": fp,
            "forbidden_claims": forbidden,
            "parse_ok": parse_ok,
            "avg_elapsed_s": mean(elapsed) if elapsed else 0.0,
            "avg_total_s": mean(total_ns) if total_ns else 0.0,
            "avg_load_s": mean(load_ns) if load_ns else 0.0,
            "avg_prompt_s": mean(prompt_ns) if prompt_ns else 0.0,
            "tok_s": (eval_toks / (eval_ns / 1_000_000_000)) if eval_ns else 0.0,
            "warmup_s": warmup.get("elapsed_s"),
            "warmup_load_s": ns_to_s(warm_metrics.get("load_duration")) if warm_metrics else None,
        })
    rows.sort(key=lambda r: (r["avg_score"], r["recall"], -r["avg_elapsed_s"]), reverse=True)
    return rows


def print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["rank", "model", "ctx", "score", "recall", "prec", "match", "fp", "claims", "json", "wall", "load", "prompt", "tok/s", "warm"]
    print("\t".join(headers))
    for i, r in enumerate(rows, 1):
        print("\t".join([
            str(i), r["model"], str(r.get("num_ctx") or ""), f"{r['avg_score']:.3f}", f"{r['recall']:.3f}", f"{r['precision']:.3f}",
            f"{r['matched']}/{r['expected']}", str(r["false_positives"]), str(r["forbidden_claims"]),
            f"{r['parse_ok']}/{r['cases']}", f"{r['avg_elapsed_s']:.1f}", f"{r['avg_load_s']:.1f}",
            f"{r['avg_prompt_s']:.1f}", f"{r['tok_s']:.1f}", "" if r.get("warmup_s") is None else f"{r['warmup_s']:.1f}",
        ]))


def html_report(rows: list[dict[str, Any]], records: list[dict[str, Any]], notes: str | None = None) -> str:
    scoring_records = [r for r in records if r.get("record_type") != "warmup"]
    notes_html = f"<h2>Findings</h2>\n<pre>{html.escape(notes)}</pre>" if notes else ""
    row_html = []
    for i, r in enumerate(rows, 1):
        row_html.append(f"""
<tr>
<td>{i}</td><td>{html.escape(r['model'])}</td><td>{html.escape(str(r.get('num_ctx') or ''))}</td><td>{r['avg_score']:.3f}</td><td>{r['recall']:.3f}</td><td>{r['precision']:.3f}</td>
<td>{r['matched']}/{r['expected']}</td><td>{r['false_positives']}</td><td>{r['forbidden_claims']}</td><td>{r['parse_ok']}/{r['cases']}</td>
<td>{r['avg_elapsed_s']:.1f}</td><td>{r['avg_load_s']:.1f}</td><td>{r['avg_prompt_s']:.1f}</td><td>{r['tok_s']:.1f}</td><td>{'' if r.get('warmup_s') is None else f'{r["warmup_s"]:.1f}'}</td>
</tr>""")
    details = []
    for r in scoring_records:
        s = r["score"]
        m = r.get("ollama_metrics") or {}
        details.append(f"""
<details>
<summary>{html.escape(r['model'])} / {html.escape(r['case_id'])}: score {s['score']:.3f}, matched {s['matched_count']}/{s['expected_count']}, fp {s['false_positive_count']}, wall {r.get('elapsed_s', 0):.1f}s, load {ns_to_s(m.get('load_duration')):.1f}s</summary>
<h4>Misses</h4><pre>{html.escape(json.dumps(s.get('misses', []), indent=2))}</pre>
<h4>False positives</h4><pre>{html.escape(json.dumps(s.get('false_positives', []), indent=2))}</pre>
<h4>Metrics</h4><pre>{html.escape(json.dumps(m, indent=2))}</pre>
<h4>Parsed</h4><pre>{html.escape(json.dumps(r.get('parsed'), indent=2))}</pre>
<h4>Raw</h4><pre>{html.escape(r.get('raw_response') or '')}</pre>
</details>""")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Code Review Model Benchmark</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 1600px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: .35rem .5rem; text-align: right; }}
th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
th {{ background: #eee; position: sticky; top: 0; }}
pre {{ white-space: pre-wrap; background: #f6f6f6; padding: .75rem; overflow-x: auto; }}
details {{ margin: .75rem 0; }}
</style></head><body>
<h1>Code Review Model Benchmark</h1>
<p>Higher score is better. Score combines recall, precision, severity calibration, and a penalty for false claims like saying tests were run.</p>
<p>Timing columns: wall = average request wall-clock seconds for scored cases; load/prompt are Ollama-reported average load and prompt-eval seconds; tok/s is generated-token eval throughput. Warm is the one-time warmup request wall-clock when present.</p>
{notes_html}
<table><thead><tr><th>rank</th><th>model</th><th>ctx</th><th>score</th><th>recall</th><th>precision</th><th>matched</th><th>false positives</th><th>forbidden claims</th><th>JSON ok</th><th>wall s</th><th>load s</th><th>prompt s</th><th>tok/s</th><th>warmup s</th></tr></thead><tbody>
{''.join(row_html)}
</tbody></table>
<h2>Per-case details</h2>
{''.join(details)}
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="JSONL result paths or globs")
    ap.add_argument("--html", help="Write static HTML report")
    args = ap.parse_args()
    records = read_records(args.paths)
    rows = summarize(records)
    print_table(rows)
    if args.html:
        path = Path(args.html)
        path.parent.mkdir(parents=True, exist_ok=True)
        notes_path = path.parent / "NOTES.md"
        notes = notes_path.read_text() if notes_path.exists() else None
        path.write_text(html_report(rows, records, notes))
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
