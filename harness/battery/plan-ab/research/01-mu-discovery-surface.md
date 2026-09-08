# mu discovery surface: what exists, what is front-loaded, where a lazy-load / enforcement mechanism would attach

Read-only survey of `mu` on 2026-09-04 (researcher memo, mu-96ga9 follow-up), plus one real session log and the operator's config. Battery evidence: `harness/battery/README.md` "plan-ab" (fifteen qwen runs, zero `discover`/`t4c`/`plan` calls; driver tools `read,write,ls,edit,grep,glob,bash`).

## 1. What is injected at session start, by channel

Two channels, not the same thing.

**Channel A — the `system_prompt` string.** Composed once at session creation by `compose_system_prompt` (`crates/mu-coding/src/serve/discovery_bootstrap.rs:66-81`): an operator prompt always wins verbatim (line 73); the ~116-token `DISCOVERY_BOOTSTRAP` (lines 20-33) is injected only when recall is off, non-bare, and no operator prompt. `--append-system-prompt` is replace, not append (`crates/mu-coding/src/bin/mu.rs:148-156`, `crates/mu-coding/src/ask.rs:38-43`). The loop appends a time line each turn (`crates/mu-core/src/agent/loop_/mod.rs:2751-2754, 3449-3475`) and hands it to `provider.stream` as the system parameter (`loop_/invoke.rs:233`).

**Channel B — recall spans.** `build_project_context` iterates the daemon's recall providers (`crates/mu-coding/src/serve/handlers/session.rs:897, 1131-1149`): optional bootloader (off by default, `config.rs:448, 572`), `SubprocessRecallProvider` running `agent memory context --cwd <cwd> --tier <tier>` (`context/recall/subprocess.rs:97-104`; tier default `"identity"`, `config.rs:439, 549`), and `ProjectFileRecallProvider` reading `./MU.md`, `./AGENTS.md`, then `~/.config/mu/{MU,AGENTS}.md` whole-file (`context/recall/project_files.rs:37, 48-58, 121-153`). These become `MemoryInjection`/`FileLoad` spans at `RetentionClass::Startup` on every turn's rope (`context/assembly.rs:106-134`), rendered as System-role messages (`context/renderer.rs:108-113`). Channel B is independent of Channel A: an operator prompt does not suppress MU.md/AGENTS.md.

Measured on the operator's config (renderer estimate chars/4, `renderer.rs:349-357`): MU.md 1,737 B + AGENTS.md 9,913 B = 11,650 B. A real arm-P-shaped log confirms `"token_breakdown":{"file_load":2902,"tool_schema":2359,"user":208}` (`~/.local/share/mu/events/b30b92f345a84a49/session-1.jsonl`). Memory kernel: `--tier identity` = 1,546 B (~390 tokens); `--tier full` = 62,226 B (~15.5K tokens). Per-turn capability hints, memory hints, kx hints are all off by default (`config.rs:199, 256, 476, 497`).

## 2. What the tool list carries per request

`ToolSpec` = name, description, `input_schema`, optional `display`/`when`, runtime `policy`, `verbatim_result` (`crates/mu-core/src/agent/tool.rs:17-46`). Nothing is deferred: every turn does `tool_specs = tools.iter().map(|t| t.spec())` (`loop_/mod.rs:2309`) and passes the full vector to the provider (`mod.rs:2816`, `invoke.rs:184, 236`). Each spec is also a `ToolSchema` span (description + JSON schema, `Hot`) in the rope (`assembly.rs:147-158`).

Composition: base tools by name (`serve/factory.rs:346-387`: read, write, ls, edit, grep, glob, memory_recall, final_answer, aws_recon, bash); `mu ask` defaults to `read,grep,glob,memory_recall` + `final_answer` (`mu.rs:572-581`); per-session injection of `spawn_worker`, `mailbox`, `watch` (only if bash granted), autonomy tools (`session.rs:579-688`); `discover` always pushed last (`session.rs:933-940`); the launch grant is written into `Capability.allowed_tools` (`session.rs:696-720`). The loop's `tools` vector is fixed at spawn (`session.rs:1031`).

Size: the logged 12-tool set = 2,359 estimated tokens (~9.4 KB). Per-tool upper bounds from `fn spec`: bash ~3.2 KB, watch ~3.0 KB, spawn_worker ~1.6 KB, grep/edit/autonomy ~1.4 KB, memory_recall 1.3, glob 1.2, read/discover 1.0, final_answer 0.8, write 0.7, ls 0.5. The tool block is roughly the size of AGENTS.md, and the AGENTS.md rule to "call discover on first substantive use" (`~/.config/mu/AGENTS.md:16-30`) competes with schemas that already answer "which tool".

## 3. The `discover` tool

Spec and behaviour: `crates/mu-coding/src/tools/discover.rs:65-93, 95-191`. Manifest = sibling tools filtered by `cap.check_allow` + daemon skills + the host catalog (`t4c_source.rs:128-134, 158-169`; `EnvCatalogSource` = curated ∩ installed, 14 curated entries, `crates/t4c/src/catalog.rs:76-84, 103`). Ranking is lexical by default (`[index].semantic_discover=false`, `config.rs:182, 255`): score = count of intent terms found in path segments + summary words + keywords (`crates/t4c/src/rank.rs:65-76, 80-92`). Keywords are tokenized name + `when` hint (`t4c_source.rs:378-384`), and no tool sets `with_when`, so the only signal is name and description prose.

Result text (`discover.rs:195-220`): `Capabilities matching "<intent>" (best first, N shown):` then per line `• tool.read  (score 2.00)  [mu-tools]` + summary, with `[unavailable this session: <reason>]` when constrained. Default limit 20.

It cannot make anything available. Read-only by contract (`discover.rs:11, 93`), manifest `help: None` (`t4c_source.rs:396`), `CapabilityView` has no schema field (`t4c_source.rs:203-227`), no input path adds a tool to the loop. Layer 2 (unknown tool name → "closest available … call `discover`", `execute_tools.rs:936-953`) is gated on `[index].discover_injection`, off by default.

## 4. Skills

`load_skill_dir` parses frontmatter (`skill/loader.rs:22-39`) and eagerly reads body + `references/*.md` into `SkillActivation` spans at `RetentionClass::Pinned` (`loader.rs:131-137, 168-173`). Daemon discovers once at startup from `.mu/skills` and `~/.config/mu/skills` (`loader.rs:188-197`; `serve/mod.rs:528-541`). Operator has three (postmortem 13.8 KB, jj-working 6.5 KB, jj-runbook 5.4 KB) plus one project skill.

Progressive disclosure is half-built. Metadata reaches the model only through `discover` (`skill.<name>`, `t4c_source.rs:69-90`); no skill index is injected into the daemon's prompt. Bodies never enter a session's rope: `SkillManager::activate` → `rope.activate_skill` (`skill/mod.rs:100-111`, `context/rope.rs:457`) is called only by aws_recon (`crates/mu-coding/src/skills/aws_recon.rs:47`); the RPC surface (`serve/dispatch.rs:172-215`) has no activate method. mu-solo's `/skill` ships the body as a user message (`mu-solo/src/app.rs:5493-5513`). If a body did enter the rope, heuristic compaction would never evict it (`compaction/heuristic.rs:109-114, 470-474`).

## 5. Enforcement points

Everything runs through `handle_execute_tools` (`agent/loop_/execute_tools.rs:397-970`), per call, in fixed order: capability (`check_allow` for allowed_tools/expiry/budget, `check_effects` against `SessionConstraints`, AWS grant; lines 434-489, `capability.rs:611-672`) > retry guard (491-523; `RETRY_STREAK_LIMIT=3`, identical-error limit 5, lines 30, 52) > loop guard (530-537; `IDENTICAL_SUCCESS_LIMIT=3`, line 39) > `Tool::validate` (548-556) > permission Ask/Deny (561-703, 180 s fail-closed). Refusals are ordinary `is_error` tool results plus a Callout. Capability text (714-720): "runtime refused: tool `x` blocked by session capability (reason). … Call `discover` with your intent to find a granted alternative, ask the user to widen scope, or report the obstacle." Loop guard (739-745): "runtime refused: loop guard. This exact `x` call already succeeded N consecutive times with identical arguments … take a materially different action, or report to the user what you are stuck on." Identical results also get a runtime note (318-322).

No hooks (doc comments only; `action_recall.rs:16` refers to Claude Code's PreToolUse). `MAX_EMPTY_TURN_RETRIES=3` (`mod.rs:940, 2867-2916`) bounds actionless turns only; a refused tool call is a turn with a tool call (`mod.rs:948-950`), so refusals never end the ask. That is the A-1 runaway (mu-ucjhg).

A precondition such as "no write/edit/bash until discover or plan" attaches at 434-489: the gate already holds the session-mutable `Arc<Mutex<Capability>>` and `ToolHistory`, and `DiscoverTool` holds the same handle (`discover.rs:38, 51`), so it can lift the gate. The "not loaded" refusal attaches at the `tools.iter().find` miss (432, 936-953).

## 6. Compaction

`[compaction].default_policy` default `"heuristic"` (`config.rs:579-618, 630`), threshold from the route's soft limit (`session.rs:1042-1049`). Heuristic drop order: stale FileLoad → old tool call/result clusters → assistant turns beyond the last 2 → SkillActivation (`heuristic.rs:8-43, 73`); `evictable` is false for `Startup` and `Pinned` (`heuristic.rs:109-114`); `System`, `ToolSchema`, `MemoryInjection`, `User` preserved by kind (33-35). hash-and-summary emits its summary as `Pinned` (`compaction/hash_summary.rs:544-547`).

A small block survives if it is a `Pinned` (or `Startup`) span. What is missing is a door: the rope is rebuilt each turn from `(system_prompt, project_context, messages, tool_specs)` (`mod.rs:2350-2362`, `assembly.rs:85-90`) or appended onto a compaction baseline (`mod.rs:2351`); `project_context` is immutable for the session (`recall/mod.rs:104-113`). Capability hints show the pattern for a post-assembly insertion anchored to a message span (`mod.rs:2369-2380`, `capability_hints.rs:201-237`).

## Attachment points

1. **Programmatic gate (S).** Gate state on `Capability` (`capability.rs:32-83`) or `ToolHistory` (`execute_tools.rs:75-88`); check it in the capability block (`execute_tools.rs:434-489`) for `Mutating`/`Execute` tools until `discover`/`plan` has recorded a call; render through the 714-720 template. `DiscoverTool` clears it via its cap handle (`discover.rs:51`). Seed from config in `session.rs:961-965`.
2. **Refusal ends the turn (S).** Count consecutive all-refused tool rounds next to `MAX_EMPTY_TURN_RETRIES` (`mod.rs:940, 2867-2892`) and end the ask. Closes the A-1 loop the guards cannot.
3. **Pinned plan span (S static / M live).** New `AgentConfig` field (or `RecallSource` variant → `RetentionClass::Pinned`) inserted after recall spans in `assemble_rope_with_context` (`assembly.rs:106-135`) and on the baseline path (`mod.rs:2351`); a `plan` tool writes an `Arc<Mutex<Option<String>>>` the loop reads at assembly (the `LiveHintConfig` shape, `capability_hints.rs:96-102`).
4. **Deferred tool schemas with a stub (M).** Split `session_tools` into loaded/deferred (`session.rs:921-940`); filter `tool_specs` at `mod.rs:2309` by a session-mutable loaded set; emit one short System span listing deferred names; turn the not-found branch (`execute_tools.rs:936-953`) into "exists, not loaded, call discover/load"; have `discover` load on request. Costs: `allowed_tools` grant (`session.rs:696-720`) and prefix-cache churn when the tool list changes.
5. **In-loop skill activation (M).** A `load_skill` path that pushes `LoadedSkill.skill.spans` (already `Pinned`) into assembly; skills already ride on `DiscoverHints` (`capability_hints.rs:165`). Gives skills the second half of progressive disclosure.
