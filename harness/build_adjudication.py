#!/usr/bin/env python3
"""Build the adjudicator prompt from a panel's round-1 findings.
usage: build_adjudication.py <r1-prefix> <pr> <diff-file> <out-prompt-file>
Collects every finding from <r1-prefix>.rank*.out as a numbered claim, appends the
diff, writes the adjudication prompt, and prints the claim count (0 => nothing to
adjudicate; discovery consensus stands)."""
import json, re, glob, sys, os

def extract(s):
    s = s.strip()
    s = re.sub(r'^\s*\[thinking\].*?$', '', s, flags=re.M)
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', s, re.S)
    if m:
        s = m.group(1)
    else:
        a, b = s.find('{'), s.rfind('}')
        if a >= 0 and b > a:
            s = s[a:b+1]
    return json.loads(s)

prefix, pr, difff, outf = sys.argv[1:5]
claims = []
for f in sorted(glob.glob(prefix + ".rank*.out")):
    who = os.path.basename(f).split('.')[-2]  # model tag
    try:
        d = extract(open(f).read())
    except Exception:
        continue
    for x in d.get('findings', []):
        claims.append((who, x.get('severity', '?'), x.get('file', '?'), x.get('line', '?'), x.get('issue', '')))

hdr = ('You are the ADJUDICATOR on a code-review panel for `mu` (a Rust agent runtime), '
       f'reviewing PR #{pr}. Other reviewers raised the claims below. You have read-only access '
       '(Read/Grep) to the repository at its PRE-change state in the cwd, plus the PR diff. For EACH '
       'claim, verify it against the ACTUAL code and rule CONFIRMED / REFUTED / UNCERTAIN, citing '
       'file:line evidence and one sentence of reasoning.\n\n'
       'Respond with ONLY one JSON object (no prose, no fence):\n'
       '{"claims":[{"id":<int>,"ruling":"CONFIRMED|REFUTED|UNCERTAIN","evidence":"<file:line>",'
       '"why":"<one sentence>"}],"overall_verdict":"approve|needs-changes","summary":"<2 sentences>"}\n\n'
       'Claims to adjudicate:')
lines = [hdr] + [f"{i}. [{w}, {s}] {fl}:{ln} — {iss}" for i, (w, s, fl, ln, iss) in enumerate(claims, 1)]
with open(outf, 'w') as fh:
    fh.write("\n".join(lines))
    fh.write(f"\n\nPR #{pr} diff under review:\n```diff\n")
    fh.write(open(difff).read())
    fh.write("\n```\n")
print(len(claims))
