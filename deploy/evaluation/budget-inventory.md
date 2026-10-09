# Native evaluation budget inventory

Set `EVALUATION_BUDGET_INVENTORY_PATH` to an administrator-owned JSON file using
`budget-inventory.example.json`. The checked-in example is executable by the real
loader. Replace endpoint IDs with exact existing database endpoint IDs and group
all endpoint aliases for the same provider account under the same `pool`. Origins
must match the native profile exactly; OpenAI uses `https://api.openai.com/v1`.
No credentials belong in this file. Provider credentials remain in their existing
scoped endpoint store. HTTP model settings cannot define a trusted ceiling.

The three code-reviewed profiles support text and client-executed functions only.
They reserve the full documented input ceiling plus the final generated-output
limit: native GPT-4.1 snapshot `gpt-4.1-2025-04-14` (1,047,576 + up to 32,768),
Claude Haiku 4.5 snapshot `claude-haiku-4-5-20251001` (200,000 + up to 64,000), and
Gemini 2.5 Flash `gemini-2.5-flash` (1,048,576 + up to 65,536). This deliberately
coarse reservation can require a large batch budget for a short prompt. Images,
remote files, provider server tools, multiple generated candidates, and unaccounted
request overrides are unavailable in these profiles. Ordinary unregistered models
remain available outside strict evaluation; this file does not grant them a hard
Token guarantee.

The code also contains `acceptance-chat-v1` for the owned deterministic fixture
`http://acceptance-inference:8080/v1` / `acceptance-chat`, reserving 262,144 input
Tokens plus at most 4,096 output Tokens. This test profile does not authorize an
arbitrary gateway or production model.

The native profile evidence below was checked on 2026-09-10; these are fixed
code-reviewed ceilings, not a claim about current provider catalogs or prices:

- [GPT-4.1 model and snapshot](https://developers.openai.com/api/docs/models/gpt-4.1)
- [Chat Completions output and choice accounting](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Claude model limits](https://platform.claude.com/docs/en/models/overview)
- [Gemini 2.5 Flash limits](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash)

`price: null` intentionally provides Token-only authority. A money-limited suite
requires deployment-owned fixed `PriceSnapshot` rates for every applicable input,
output, cache-read, cache-write (Claude), and reasoning dimension. Do not infer
missing prices from zero or current model hints. Supply reviewed rates under a new
inventory revision; no illustrative rates are included in the example.

Configuration publication captures the inventory fingerprint and every permitted
fallback from the actual execution resilience policy, including quota fallback
(enabled by default). A required candidate lacking support blocks publication.
Metadata checks borrow the caller transaction and never decrypt credentials or
contact providers. The credential-presence check is conservative: a stored but
unusable credential may be included and later fail runtime secret resolution.

Changing any inventory content or candidate metadata invalidates old proof for new
hard evaluation admission. Publish a new configuration; immutable old versions are
never rewritten. Configurations published without inventory stay explicitly
unavailable for hard-budget evaluation. The current batch scheduler and model-judge service bind that frozen authority
in their trusted kernel admission transaction. Source/budget binding, execution
slot preparation and initial inbox enqueue commit together; physical quota is
reserved separately before each actual send. Registering this inventory alone
does not publish a configuration, schedule a batch or reserve quota.
