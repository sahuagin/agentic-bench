#!/usr/bin/env python3
"""
battery2-score — retention scorer, mu-316wl. For each run's stdout, count how
many of the four planted (key, value) pairs were recalled correctly. Score =
correct/4. A context-pressure failure shows as a MIDDLE needle missing/wrong
while the first and last survive.

  battery2-score.py <runs_dir>
"""
import sys, os, re, glob

EXPECT = {"ALPHA_SEED": "46200", "BETA_SEED": "89901",
          "GAMMA_SEED": "57457", "DELTA_SEED": "12674"}

def score_file(path):
    txt = open(path, errors="replace").read()
    got = {}
    for k, v in EXPECT.items():
        # accept KEY=value or KEY: value, optional >>> markers, commas stripped
        m = re.search(rf"{k}\s*[:=]\s*>*\s*([0-9,]+)", txt)
        got[k] = (m.group(1).replace(",", "") == v) if m else False
    return got

def main(runs):
    rows = []
    for out in sorted(glob.glob(os.path.join(runs, "b2-*.out"))):
        label = os.path.basename(out)[:-4]
        got = score_file(out)
        n = sum(got.values())
        missing = [k.split("_")[0] for k, ok in got.items() if not ok]
        rows.append((label, n, missing))
    for label, n, missing in rows:
        miss = "" if not missing else "  missing/wrong: " + ",".join(missing)
        print(f"{label:28s} {n}/4{miss}")
    # aggregate by arm×size
    agg = {}
    for label, n, _ in rows:
        # b2-<arm>-<size>-<rep>
        parts = label.split("-")
        if len(parts) >= 4:
            key = f"{parts[1]}-{parts[2]}"
            agg.setdefault(key, []).append(n)
    print("\n=== retention by arm-size (mean of /4) ===")
    for key in sorted(agg):
        vals = agg[key]
        print(f"{key:20s} n={len(vals)}  scores={vals}  mean={sum(vals)/len(vals):.2f}/4")

if __name__ == "__main__":
    main(sys.argv[1])
