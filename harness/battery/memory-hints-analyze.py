#!/usr/bin/env python3
"""Per captured request: which memory-hint block (if any) reached the wire and
which planted battery-3 facts it named. Expected per probe: exactly the one
matching b3-test-* memory, no others."""
import json, sys, re
FACTS = ["zephyr7", "nimbus3", "quokka", "vireo"]
n = 0
for line in open(sys.argv[1]):
    rec = json.loads(line)
    req = rec.get("request", {})
    if "/v1/chat/completions" not in req.get("path", ""):
        continue
    n += 1
    body = req.get("body")
    if isinstance(body, str):
        try: body = json.loads(body)
        except Exception: body = {}
    msgs = body.get("messages", []) if isinstance(body, dict) else []
    user_texts = [m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")) for m in msgs if m.get("role") == "user"]
    prompt = next((t for t in user_texts if "component" in t or "protocol" in t or "batch" in t or "cache" in t), "?")
    hints = [t for t in user_texts if "[memory hints" in t]
    named = sorted({f for h in hints for f in FACTS if f"b3-test-{f}" in h})
    other = [ln for h in hints for ln in h.splitlines() if ln.startswith("•") and "b3-test-" not in ln]
    print(f"req {n}: prompt={prompt[:50]!r} hint_blocks={len(hints)} planted={named} non_planted_lines={len(other)}")
    for h in hints:
        print("   " + h.replace("\n", "\n   ")[:900])
print(f"{n} chat requests")
