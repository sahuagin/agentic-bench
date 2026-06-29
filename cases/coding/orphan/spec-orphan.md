# Task: context compaction orphans tool spans, breaking the next provider request

When context compaction runs (the `SpanFamilyDropPolicy` in the heuristic compaction path,
crates/mu-core/src/context/compaction/heuristic.rs), it can leave an ORPHANED tool span:
a tool_use (an assistant tool call) whose matching tool_result was dropped, or a tool_result
whose tool_use was dropped.

This happens when one assistant turn fans out to multiple tool calls whose results are
parallel, non-contiguous, or reordered in the rope. The consequence: the *next* provider
request is rejected — OpenAI: "No tool output found for function call"; Anthropic:
tool_use/tool_result mismatch.

Invariant to restore: a tool_use and ALL of its tool_results form one logical exchange and
must share drop-status — the whole exchange is dropped together, or kept together. Compaction
must never drop one part of an exchange while keeping another, for any ordering or fan-out.

(The call ids that link a tool_use to its results are already present in the rope: an assistant
span exposes its tool_use blocks, and a tool_result span's id has the form
`...-tool-result:<call_id>`.)

Fix the policy so compaction never orphans a tool span. Keep the change minimal and within the
compaction heuristic.
