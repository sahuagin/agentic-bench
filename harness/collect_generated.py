#!/usr/bin/env python3
"""Collect every model's generated code from a case run into a durable, annotated
tree the operator can review: results/generated/<run>/<model>.diff (each prefixed
with a model/case/run/verdict header) + MANIFEST.md. Reads the run's results jsonl
(model + verdict/score) and the per-model diffs the runner left in ~/cbench.

  ./collect_generated.py results/hardcode-20260630-031500.jsonl hardcode
"""
import sys, json, pathlib, os

def main():
    jsonl = pathlib.Path(sys.argv[1])
    label = sys.argv[2] if len(sys.argv) > 2 else "case"
    cbench = pathlib.Path(os.path.expanduser("~/cbench"))
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    run = jsonl.stem  # e.g. hardcode-20260630-031500
    outd = ROOT / "results" / "generated" / run
    outd.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
    man = [f"# Generated code — {label} — {run}", "",
           "| model | verdict | score | diff lines | file |", "|---|---|---|---|---|"]
    saved = 0
    for r in rows:
        m = r.get("model", "?")
        slug = "hc_" + m.replace(":", "_").replace("/", "_").replace(".", "")
        df = cbench / f"{slug}.diff"
        safe = m.replace(":", "_").replace("/", "_")
        if not df.exists() or not df.read_text(errors="replace").strip():
            man.append(f"| `{m}` | {r.get('verdict')} | {r.get('score')} | — | (no diff) |")
            continue
        diff = df.read_text(errors="replace")
        header = (f"// ===== GENERATED CODE (agentic-bench) =====\n"
                  f"// case:    {label}\n// model:   {m}\n// run:     {run}\n"
                  f"// verdict: {r.get('verdict')}   score: {r.get('score')}\n"
                  f"// diff_lines: {r.get('diff_lines')}   wall_s: {r.get('wall_s')}\n"
                  f"// evidence: {str(r.get('evidence',''))[:200]}\n"
                  f"// ==========================================\n\n")
        (outd / f"{safe}.diff").write_text(header + diff)
        man.append(f"| `{m}` | {r.get('verdict')} | {r.get('score')} | {r.get('diff_lines')} | `{safe}.diff` |")
        saved += 1
    (outd / "MANIFEST.md").write_text("\n".join(man) + "\n")
    print(f"collected {saved} diffs -> {outd}")
    print("\n".join(man[2:]))

if __name__ == "__main__":
    sys.exit(main())
