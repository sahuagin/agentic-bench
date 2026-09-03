#!/usr/bin/env python3
"""battery-3 scorer: did the run recall the planted value? mu-316wl."""
import sys, os, re, glob

VAL = {"zephyr": "88231", "nimbus": "5417", "quokka": "92", "vireo": "73"}

def hit(path, fact):
    t = open(path, errors="replace").read()
    # exact value as a standalone token (avoid matching inside longer numbers)
    return re.search(rf"(?<!\d){VAL[fact]}(?!\d)", t) is not None

def main(runs):
    agg = {}
    for out in sorted(glob.glob(os.path.join(runs, "b3-*.out"))):
        lbl = os.path.basename(out)[:-4]
        _, cond, fact, rep = lbl.split("-")
        ok = hit(out, fact)
        agg.setdefault((cond, fact), []).append(ok)
    conds = sorted({c for c, _ in agg})
    facts = ["zephyr", "nimbus", "quokka", "vireo"]
    print(f"{'':8s}" + "".join(f"{f:>9s}" for f in facts) + "   overall")
    for c in conds:
        cells = []
        tot = 0; n = 0
        for f in facts:
            v = agg.get((c, f), [])
            k = sum(v); n += len(v); tot += k
            cells.append(f"{k}/{len(v)}")
        print(f"{c:8s}" + "".join(f"{cc:>9s}" for cc in cells) + f"   {tot}/{n}")

if __name__ == "__main__":
    main(sys.argv[1])
