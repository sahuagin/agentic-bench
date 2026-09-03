#!/usr/bin/env python3
"""T0: is the HTML file complete (closing tag near the end)? Also extracts the
inline scripts to <out.js> for the T1 `node --check` parse."""
import re, sys
h = open(sys.argv[1], errors="replace").read()
s = [x for x in re.findall(r"<script[^>]*>(.*?)</script>", h, re.S) if x.strip()]
open(sys.argv[2], "w").write("\n;\n".join(s))
print("T0=PASS" if "</html>" in h[-300:] else "T0=TRUNC", end=" ")
