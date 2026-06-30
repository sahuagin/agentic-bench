# Task: make action_recall's danger-verb triggers data-driven (retire the hardcoded const)

`crates/mu-coding/src/tools/action_recall.rs` hardcodes the point-of-action
trigger set as a compile-time `const DANGER_VERBS: &[DangerVerb]` (11 entries:
rm, git push --force, jj abandon, jj op restore, sed -i, cargo publish,
git reset --hard, git checkout --, br close, gh pr merge, gh pr close). Adding,
removing, or tuning a trigger currently requires editing Rust, rebuilding, and
redeploying the binary on every machine. The trigger table is pure policy data
(a prefix + an optional required flag + a memory query) — there is no reason it
lives baked into the binary.

Make the trigger source **data-driven**, with these requirements:

- **Cross-machine authoritative source.** One change takes effect fleet-wide with
  no per-host edit. etcd is available at the standard cluster endpoint and is the
  preferred source; a TOML config file is acceptable for small/standalone
  deployments. A per-host local file *alone* fails the cross-machine requirement.
- **In-binary seed fallback.** Keep the current 11 triggers as a named seed
  default, used only when no external source provides triggers.
- **Fail-open.** If the source is missing, unreachable, or malformed, fall back to
  the seed and NEVER gate the action path. Malformed individual entries are
  skipped, not fatal.
- **Built once** at startup (or on a watch/TTL refresh), not per command — the
  per-command path stays lexical + within the existing 1s budget.
- **Retire `const DANGER_VERBS` as the sole source** — demote it to the named
  seed-default; the live table must come from the data-driven source when present.
- Preserve the existing advise-once / 1s-timeout / `MU_NO_ACTION_RECALL`
  kill-switch behavior.

Reference: `crates/mu-coding/src/tools/action_recall.rs` (the const at ~line 64,
`match_danger_verb` ~line 124). An existing pluggable-backend shape lives in
`crates/mu-coding/src/serve/discovery/` (a trait with a seed/local default and a
sketched etcd backend); reuse that shape rather than inventing a parallel one.
