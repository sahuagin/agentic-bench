#!/usr/bin/env python3
"""score — did the model exercise the plan / test / act-on-failure loop?

  python3 score.py <capture.jsonl> [<mu-ask.stderr>]

Reads the LAST /v1/chat/completions request on the wire (openai-chat shape:
its messages[] carry the whole conversation) and prints one line of
key=value fields:
  requests        exchanges on the wire (= model round trips)
  turns           assistant messages in the final conversation
  calls           tool calls, by name
  plan_sets       `plan set` invocations (arm A can; arm B has no such tool)
  t4c             `t4c find|help|run|...` invocations (arm T: the discovery path)
  plan_other      other `plan` invocations (show, --help)
  cargo_tests     bash commands that ran cargo test
  fails           tool results carrying mu's failure marker (`exit: <code>`) or is_error text
  first_fail      index (in tool results) of the first failing result, or -
  edits_after_fail  write/edit calls after the first failing result
  tests_after_fail  cargo test runs after the first failing result
  acted_on_failure  yes if a failure was followed by an edit/write AND a re-test
  last_test_ok    whether the last cargo test result in context reported success
  finished        final_answer called
  json_err        (stderr) mu-gg2yf truncated-tool-JSON warnings
Needs a NO_SCRUB=1 parse (message text intact). Local analysis only.
"""
import json, re, sys
from collections import Counter

cap = sys.argv[1]
err = sys.argv[2] if len(sys.argv) > 2 else None

reqs = []
last_resp = None
for line in open(cap):
    r = json.loads(line)
    b = r["request"]["body"]
    if r["request"]["path"].endswith("/chat/completions") and isinstance(b, dict) and "messages" in b:
        reqs.append(b); last_resp = r["response"]
if not reqs:
    print("requests=0 (no chat completions on the wire)"); sys.exit(0)

msgs = list(reqs[-1]["messages"])
# The final assistant turn is only in the last RESPONSE (the request that
# carried it never went out). Fold its tool-call names back in from the SSE.
final_calls = []
for ev in (last_resp or {}).get("sse_events") or []:
    try:
        d = json.loads(ev["data"])
    except (ValueError, TypeError):
        continue
    for ch in d.get("choices") or []:
        for tc in (ch.get("delta") or {}).get("tool_calls") or []:
            name = (tc.get("function") or {}).get("name")
            if name:
                final_calls.append({"id": tc.get("id") or f"final{len(final_calls)}",
                                    "function": {"name": name, "arguments": ""}})
if final_calls or last_resp:
    msgs.append({"role": "assistant", "content": "", "tool_calls": final_calls})
# Reconstruct the tool-call/result sequence in order.
calls = []            # (name, args_str)
results = []          # (call_name, content)
by_id = {}
for m in msgs:
    if m["role"] == "assistant":
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            name = fn.get("name", "?"); args = fn.get("arguments", "")
            calls.append((name, args)); by_id[tc.get("id")] = name
    elif m["role"] == "tool":
        c = m.get("content")
        if not isinstance(c, str):
            c = json.dumps(c)
        results.append((by_id.get(m.get("tool_call_id"), "?"), c))

def cmd_of(args):
    try:
        return json.loads(args).get("command", "") or ""
    except Exception:
        return args or ""

bash_cmds = [cmd_of(a) for n, a in calls if n == "bash"]
plan_sets = sum(1 for c in bash_cmds if re.search(r"\bplan\s+set\b", c))
t4c_calls = sum(1 for c in bash_cmds if re.search(r"\bt4c\s+(find|help|run|walk|list)\b", c))
plan_help = sum(1 for c in bash_cmds if re.search(r"\bplan\b", c) and not re.search(r"\bplan\s+set\b", c))
cargo_re = re.compile(r"\bcargo\s+(test|t)\b")
cargo_tests = sum(1 for c in bash_cmds if cargo_re.search(c))

def failed(content):
    return bool(re.search(r"^exit: -?\d+\s*$", content, re.M)) or content.startswith("bash:") \
        or "test result: FAILED" in content or "error[E" in content or "error: could not compile" in content

# Walk calls and results in lockstep (each call gets one result, in order).
first_fail = None
edits_after = tests_after = 0
last_test_ok = "-"
fails = 0
for i, ((name, args), (_, content)) in enumerate(zip(calls, results)):
    is_fail = name == "bash" and failed(content)
    if is_fail:
        fails += 1
        if first_fail is None:
            first_fail = i
    if name == "bash" and cargo_re.search(cmd_of(args)):
        last_test_ok = "no" if is_fail else ("yes" if "test result: ok" in content else "?")
    if first_fail is not None and i > first_fail:
        if name in ("write", "edit"):
            edits_after += 1
        if name == "bash" and cargo_re.search(cmd_of(args)):
            tests_after += 1

names = Counter(n for n, _ in calls)
finished = "yes" if names.get("final_answer") else "no"
acted = "yes" if (first_fail is not None and edits_after and tests_after) else ("n/a" if first_fail is None else "no")
turns = sum(1 for m in msgs if m["role"] == "assistant")
json_err = "-"
if err:
    try:
        json_err = sum(1 for l in open(err, errors="replace") if "failed to parse tool input JSON" in l)
    except OSError:
        pass
fields = [
    f"requests={len(reqs)}", f"turns={turns}",
    "calls=" + ",".join(f"{k}:{v}" for k, v in sorted(names.items())),
    f"plan_sets={plan_sets}", f"t4c={t4c_calls}", f"plan_other={plan_help}", f"cargo_tests={cargo_tests}", f"fails={fails}",
    f"first_fail={'-' if first_fail is None else first_fail}",
    f"edits_after_fail={edits_after}", f"tests_after_fail={tests_after}",
    f"acted_on_failure={acted}", f"last_test_ok={last_test_ok}",
    f"finished={finished}", f"json_err={json_err}",
]
print(" ".join(fields))
