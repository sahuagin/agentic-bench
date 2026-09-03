#!/usr/bin/env python3
"""One-line T3 summary from a web_probe.py --json result."""
import json, sys
r = json.load(open(sys.argv[1])); v = sys.argv[2]; size = sys.argv[3]
boot_exc = r["exceptions"][: r.get("boot_exception_count", 0)]
boot = "OK" if not boot_exc else "ERR:" + boot_exc[0][:80]
render = "ANIM" if r.get("animating") else ("STATIC" if r.get("render_nonblank") else "BLANK")
inp = "RESP" if r.get("responded_to_input") else "DEAD"
print(f"T3={v} boot={boot} raf={r.get('raf_per_s')} render={render} input={inp} input_errors={len(r.get('input_errors', []))} size={size}B")
