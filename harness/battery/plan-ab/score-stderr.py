#!/usr/bin/env python3
"""score-stderr — the score.py fields that can be read from `mu ask` stderr alone,
for lanes with no capture proxy (direct provider runs).

  python3 score-stderr.py <mu-ask.stderr>

stderr carries one `[tool] <name> <json-args>` line per call and one
`[tool result: ok|err]` line per result, in order. No message text, so
`turns`/`requests` are approximated by tool calls.
"""
import json, re, sys
from collections import Counter

calls, results = [], []
json_err = 0
for line in open(sys.argv[1], errors="replace"):
    if line.startswith("[tool] "):
        name, _, args = line[7:].rstrip("\n").partition(" ")
        calls.append((name, args))
    elif line.startswith("[tool result: "):
        results.append("err" in line[14:18])
    elif "failed to parse tool input JSON" in line:
        json_err += 1

def cmd_of(args):
    try:
        return json.loads(args).get("command", "") or ""
    except Exception:
        return args or ""

names = Counter(n for n, _ in calls)
bash = [cmd_of(a) for n, a in calls if n == "bash"]
cargo_re = re.compile(r"\bcargo\s+(test|t)\b")
plan_sets = sum(1 for c in bash if re.search(r"\bplan\s+set\b", c))
t4c = sum(1 for c in bash if re.search(r"\bt4c\s+(find|help|run|walk|list)\b", c))
cargo_tests = sum(1 for c in bash if cargo_re.search(c))
err_results = sum(1 for r in results if r)
first_err = next((i for i, r in enumerate(results) if r), None)
edits_after = tests_after = 0
if first_err is not None:
    for i, (n, a) in enumerate(calls[first_err + 1:], first_err + 1):
        if n in ("write", "edit"):
            edits_after += 1
        if n == "bash" and cargo_re.search(cmd_of(a)):
            tests_after += 1
print(" ".join([
    f"tool_calls={len(calls)}",
    "calls=" + ",".join(f"{k}:{v}" for k, v in sorted(names.items())),
    f"plan_sets={plan_sets}", f"t4c={t4c}", f"discover={names.get('discover', 0)}",
    f"cargo_tests={cargo_tests}", f"err_results={err_results}",
    f"first_err={'-' if first_err is None else first_err}",
    f"edits_after_err={edits_after}", f"tests_after_err={tests_after}",
    f"finished={'yes' if names.get('final_answer') else 'no'}", f"json_err={json_err}",
]))
