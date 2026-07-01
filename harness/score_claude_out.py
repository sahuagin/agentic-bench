#!/usr/bin/env python3
"""Score the cc-subagent (opus/sonnet) review JSONs against the same cases +
scorer as review_runner, emitting runner-compatible result JSONL per model."""
import json, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from review_runner import score  # noqa: E402
ROOT = HERE.parent
d = json.loads((ROOT / 'cases/code-review/cases-final.json').read_text())
cases = {c['id']: c for c in (d if isinstance(d, list) else d.get('cases', []))}
outdir = ROOT / 'results/lens-study/claude/out'
rows_by_model = {}
missing = []
for cid in cases:
    for model in ('sonnet', 'opus'):
        p = outdir / f'{cid}__{model}.json'
        if not p.exists():
            missing.append(p.name); continue
        raw = p.read_text()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        s = score(cases[cid], parsed, raw)
        row = {'model': f'claude:{model}', 'provider': 'claude-subagent', 'case': cid,
               'provenance': cases[cid].get('provenance'), 'rep': 1, 'wall_s': None,
               'error': None, 'label': f'gen-{model}', **s}
        if parsed and isinstance(parsed.get('findings'), list):
            row['findings'] = parsed['findings']
        rows_by_model.setdefault(model, []).append(row)
ts = time.strftime('%Y%m%d-%H%M%S')
for model, rows in rows_by_model.items():
    out = ROOT / 'results' / f'review-{ts}-gen-{model}.jsonl'
    out.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    ok = [r for r in rows if r['parse_ok']]
    sc = sum(r['score'] for r in ok) / len(ok) if ok else 0
    rec = sum(r['matched'] for r in ok); exp = sum(r['expected'] for r in ok)
    fp = sum(r['fp'] for r in ok)
    print(f"claude:{model:<8} cases={len(rows)} parse_ok={len(ok)} score={sc:.3f} recall={rec}/{exp} fp={fp} -> {out.name}")
if missing:
    print(f"MISSING {len(missing)}: {missing[:6]}")
