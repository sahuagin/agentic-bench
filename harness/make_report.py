#!/usr/bin/env python3
"""Render the code-review-bench findings as a self-contained HTML page for
mu-analytics (mu console serves static HTML from ~/mu-stats/). Reads the run
JSONLs, recomputes the combined leaderboard, and embeds the verified narrative.

Usage: python3 make_report.py <local.jsonl> <hosted.jsonl> [-o out.html]
The leaderboard is DATA (computed from the runs). The narrative below it is the
operator-verified interpretation (precision artifact, capable-cluster split).
"""
import json, sys, html, argparse
from collections import defaultdict
from datetime import datetime, timezone

HOSTED = {"gpt-5.5", "claude-sonnet-4-6", "deepseek/deepseek-v4-pro",
          "z-ai/glm-5.2", "qwen/qwen3.7-max"}
COST = {
    "gpt-5.5": "$0 (codex sub)", "claude-sonnet-4-6": "$0 (claude sub)",
    "deepseek/deepseek-v4-pro": "OpenRouter ~$0.05", "z-ai/glm-5.2": "OpenRouter ~$0.20",
    "qwen/qwen3.7-max": "OpenRouter ~$0.20",
}

def load(path):
    return [json.loads(l) for l in open(path) if l.strip()]

def aggregate(records):
    by = defaultdict(list)
    for r in records:
        by[r["model"]].append(r)
    rows = []
    for m, rs in by.items():
        n = len(rs)
        matched = sum(r["score"]["matched_count"] for r in rs)
        expected = sum(r["score"]["expected_count"] for r in rs)
        fp = sum(r["score"]["false_positive_count"] for r in rs)
        json_ok = sum(1 for r in rs if r["score"]["parse_ok"])
        timeouts = sum(1 for r in rs if r.get("error") == "timeout")
        avg_score = sum(r["score"]["score"] for r in rs) / n
        # score excluding load-timeout cases (cold-load artifact, not quality)
        clean = [r for r in rs if r.get("error") != "timeout"]
        adj_score = (sum(r["score"]["score"] for r in clean) / len(clean)) if clean else avg_score
        rows.append({
            "model": m, "n": n, "score": avg_score, "adj_score": adj_score,
            "recall": matched / max(1, expected), "prec": matched / max(1, matched + fp),
            "fp": fp, "json_ok": f"{json_ok}/{n}", "timeouts": timeouts,
            "hosted": m in HOSTED, "cost": COST.get(m, "$0 (local)"),
        })
    rows.sort(key=lambda r: r["adj_score"], reverse=True)
    return rows

def esc(s): return html.escape(str(s))

def table(rows):
    out = ['<table><thead><tr>'
           '<th>#</th><th>model</th><th>where</th><th>score</th><th>adj*</th>'
           '<th>recall</th><th>prec†</th><th>fp†</th><th>clean JSON</th><th>cost</th>'
           '</tr></thead><tbody>']
    for i, r in enumerate(rows, 1):
        cls = "hosted" if r["hosted"] else "local"
        note = ' <span class=flag>load-timeout</span>' if r["timeouts"] else ''
        adj = f'{r["adj_score"]:.3f}' if abs(r["adj_score"]-r["score"])>0.001 else '—'
        out.append(
            f'<tr class={cls}><td class=num>{i}</td><td><code>{esc(r["model"])}</code>{note}</td>'
            f'<td>{cls}</td><td class=num>{r["score"]:.3f}</td><td class=num>{adj}</td>'
            f'<td class=num>{r["recall"]:.3f}</td><td class=num>{r["prec"]:.3f}</td>'
            f'<td class=num>{r["fp"]}</td><td class=num>{esc(r["json_ok"])}</td>'
            f'<td>{esc(r["cost"])}</td></tr>')
    out.append('</tbody></table>')
    return '\n'.join(out)

NARRATIVE = """
<h2>What this run actually shows</h2>
<p class=lead>Every viable local model matched the hosted frontier on the bugs
themselves. The question "are local models capable enough to replace the paid
hosted reviewers?" is answered <b>yes</b>.</p>
<ul>
<li><b>Precision numbers are a scorer artifact, not model behavior.</b> 14 of 18
flagged false-positives are the single <code>go_goroutine_capture</code> case —
where every model (local <em>and</em> hosted) correctly reported the loop-variable
capture bug + missing <code>WaitGroup</code>, but the case seeds only the
discarded-errors finding. Those "false positives" are real bugs the bench doesn't
credit. Verified by reading all 14 verbatim findings. <b>†</b> the prec/fp columns
are therefore depressed roughly uniformly and should not be used to rank.</li>
<li><b>One genuine failure: <code>deepseek-r1:32b</code>.</b> Its reasoning trace
breaks the strict-JSON contract (outright parse failure on
<code>python_claim_discipline</code>) and it under-reports (1 of 3 goroutine bugs).
A real disqualifier for a structured reviewer — not an artifact.</li>
<li><b>The bench cannot rank the capable cluster.</b> 5 cases, under-seeded,
precision corrupted → the 0.90–0.95 span is noise. Honest signal is binary:
capable (everyone except deepseek-r1) vs unreliable (deepseek-r1). <b>*</b> adj
score excludes cold-load timeouts (gpt-oss:120b, glm-4.7-flash:bf16 case 1).</li>
</ul>

<h2>Operational fallback (when claude -p becomes paid)</h2>
<p>Quality across the cluster is a wash, so the local-fallback choice is
operational: what co-resides with the resident primary
(<code>qwen3.6:27b</code>, 42GB @262k → ~31GB free) and loads fast.</p>
<table><thead><tr><th>local fallback</th><th>VRAM</th><th>clean JSON</th>
<th>goroutine bugs found</th><th>co-resident with primary?</th><th>verdict</th></tr></thead><tbody>
<tr class=local><td><code>gpt-oss:20b</code></td><td class=num>13GB</td><td>✓</td><td class=num>2/3</td><td>✓ comfortable</td><td><b>top pick</b> — drop-in 2nd reviewer</td></tr>
<tr class=local><td><code>gemma4:31b</code></td><td class=num>19GB</td><td>✓</td><td class=num>3/3</td><td>✓</td><td>higher-recall alternate</td></tr>
<tr class=local><td><code>glm-4.7-flash:q8_0</code></td><td class=num>31GB</td><td>✓</td><td class=num>1/3</td><td>tight</td><td>marginal</td></tr>
<tr class=local><td><code>qwen3.6:35b-a3b-q8_0</code></td><td class=num>38GB</td><td>✓</td><td class=num>3/3</td><td>no</td><td>vs-27b A/B — undecided (within noise; needs powered test)</td></tr>
<tr class=local><td><code>gpt-oss:120b</code> / <code>glm-4.7-flash:bf16</code></td><td class=num>65/59GB</td><td>✓</td><td>—</td><td>no (13-min load)</td><td>operationally out</td></tr>
<tr class=local><td><s><code>deepseek-r1:32b</code></s></td><td class=num>19GB</td><td>✗ parse-fail</td><td class=num>1/3</td><td>—</td><td>excluded (unreliable)</td></tr>
</tbody></table>

<h2>Caveats</h2>
<p>This rests on a 5-case bench that mis-scores its own cases (under-seeded
<code>go_goroutine_capture</code>). The ranking <em>within</em> the capable cluster
is not trustworthy; the capable-vs-unreliable split and the operational facts are.
Real ranking confidence needs more cases (mined from PR write-ups + beads:
bugs found/fixed, architecture-violation rewrites) and corrected seeds.</p>
"""

def render(rows, sources):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    style = """
    body{background:#0f1115;color:#d7dae0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:2rem;max-width:1100px}
    h1{font-size:1.4rem;margin:0 0 .2rem} h2{font-size:1.05rem;margin:1.6rem 0 .5rem;color:#fff;border-bottom:1px solid #2a2f3a;padding-bottom:.25rem}
    .sub{color:#8b93a3;margin:0 0 1.2rem} .lead{font-size:1.02rem}
    table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem;font-size:13px}
    th,td{padding:.35rem .6rem;border-bottom:1px solid #232833;text-align:left}
    th{color:#9aa3b2;font-weight:600} td.num{text-align:right;font-variant-numeric:tabular-nums}
    tr.hosted td:first-child~td:nth-child(2){} tr.hosted{background:#13181f} tr.local{background:#0f1115}
    code{background:#1a1f29;padding:.05rem .35rem;border-radius:4px;color:#9ecbff;font-size:12px}
    .flag{color:#e0a458;font-size:11px} s{color:#6b7280}
    ul{margin:.3rem 0 1rem} li{margin:.35rem 0} b{color:#fff}
    .prov{color:#6b7280;font-size:12px;margin-top:2rem;border-top:1px solid #232833;padding-top:.6rem}
    nav.site{margin:-1rem 0 1.2rem;font-size:13px;border-bottom:1px solid #2a2f3a;padding-bottom:.6rem}
    nav.site a{margin-right:1.1rem;color:#6ea8fe;text-decoration:none} nav.site a.here{color:#fff;font-weight:600}
    """
    src = ', '.join(f'<code>{esc(s)}</code>' for s in sources)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>code-review-bench — reviewer leaderboard</title><style>{style}</style></head>
<body>
<nav class=site><a href="/mu-stats/dashboard.html">Sessions</a><a href="/mu-stats/benchmarks.html">Benchmarks</a><a href="/mu-stats/code-review-bench.html" class=here>Review bench</a><a href="/mu-stats/tools.html">Tool usage</a><a href="/mu-stats/degradation.html">Degradation</a><a href="/mu-console/sessions">Review &amp; mark</a></nav>
<h1>code-review-bench — reviewer model leaderboard</h1>
<p class=sub>Deciding ci-aipr panel reviewers + the local fallback for when
<code>claude -p</code> becomes paid. Local models tested through
<code>mu ask --provider ollama</code> at production 262k context; hosted via
<code>mu ask</code> / <code>claude -p</code>. Same prompt + scorer for both.</p>
{table(rows)}
{NARRATIVE}
<p class=prov>Generated {stamp} from {src}. Scorer: code-review-bench (keyword
match, intentionally simple). Verification: all 18 false-positive/zero instances
read by hand. Source repo left untouched per operator.</p>
</body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("-o", "--out", default="<local-path>")
    a = ap.parse_args()
    records = []
    for r in a.runs:
        records += load(r)
    rows = aggregate(records)
    import os
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(render(rows, [os.path.basename(x) for x in a.runs]))
    print(a.out)

if __name__ == "__main__":
    main()
