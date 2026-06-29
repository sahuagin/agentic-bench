# Task: the /effort dial offers no levels for the public-key OpenAI provider

The OpenAI rework wired the public-key path (ProviderSelector::OpenaiApi, wire kind
`openai_api` -> api.openai.com/v1/responses) to OpenaiProvider. But the `/effort` dial offers
NO effort levels and no default for this `openai_api` path: it falls through to an empty
fallback, so the dial shows nothing for that provider.

This path runs gpt-5.5 over the Responses API, which shares the SAME effort vocabulary as the
existing codex gpt-5.5 path (provider kind `openai_codex`): levels low / medium / high / xhigh
(no `minimal`), default `medium`.

Fix the effort-level fallback so the `openai_api` provider kind offers the same effort levels
AND the same default as `openai_codex`. The logic lives in the route_catalog (the per-provider
effort-level fallback and the per-provider default-effort). Per-turn effort is already consumed
downstream; only the route_catalog fallback is missing. Keep the change minimal.
