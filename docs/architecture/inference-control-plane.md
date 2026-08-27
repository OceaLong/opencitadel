# Inference Control Plane

[简体中文](inference-control-plane.zh-CN.md)

OpenCitadel has one inference control plane for chat, embeddings, and reranking.
It is composed of three explicit resources:

- an endpoint owns provider, base URL, encrypted credential, visibility, and
  owner scope;
- a model belongs to one endpoint and owns its provider model name, kind,
  settings, prices, and declared capabilities;
- a binding selects one effective model for a purpose: `chat`, `embedding`, or
  `rerank`.

There is no environment-key fallback, implicit default model, or separate
vector credential path. A consumer resolves a purpose binding or fails closed
with a stable error key.

## Scope and resolution

Endpoints, models, and bindings are global or personal/team-scoped. Only an
administrator can mutate global resources. Workspace bindings override a
visible global binding; deleting the workspace override restores inheritance.
The referenced model and endpoint must both be visible in the active owner
scope.

Resolution verifies the purpose/kind contract. `chat` and `rerank` require a
chat model; `embedding` requires an embedding model with the platform dimension
of 1536. When no explicit rerank binding exists, rerank may resolve the chat
binding. Missing, inaccessible, or mismatched resources never fall through to
an unrelated provider.

## Providers and credentials

The registry is the single source of truth for supported provider/kind pairs:
OpenAI, Azure OpenAI, Ollama, Anthropic, and Gemini. Mutation validates the
pair before persistence. Ollama may run without a credential; providers that
require one fail closed when it is absent.

Credentials are stored only as versioned `fernet_v2` envelopes. API responses
expose `credential_configured`, never plaintext. Blank updates retain the
stored credential. The active encryption key protects new writes and an
explicit previous-key ring supports planned rotation.

## Capabilities and consumers

`GET /api/capabilities` projects owner-scoped availability for chat,
embeddings, rerank, A2A, Patrol, and Patrol remediation. UI and server admission
consume the same states (`available`, `degraded`, `not_configured`, `disabled`,
or `denied`) and stable reason keys.

Chat execution, codebase/knowledge/memory vectorization, and reranking resolve
the control plane at call time. Vector consumers can be individually disabled
through the active Execution Policy, but enabled consumers never read a standalone API key or
base URL. The UI guides missing capabilities to **Settings → Inference**.

## API and operations

The stable API is under `/api/inference`:

- `/endpoints` manages connection and credential ownership;
- `/models` manages typed chat or embedding models and probe operations;
- `/bindings` manages global/workspace purpose selection;
- `/status` reports the effective owner-scoped control-plane state.

For a demo seed, set `DEMO_INFERENCE_BASE_URL`,
`DEMO_INFERENCE_CREDENTIAL`, `DEMO_INFERENCE_MODEL`, and
`DEMO_INFERENCE_PROVIDER` before the API container starts. Production
credentials should be created through the authenticated API/UI and a managed
secret workflow, not committed configuration.

Inference calls execute as durable Activities. Run input records selected
model identity and stable failure categories; credentials and raw private input
are excluded from public events. Retry and fallback behavior is described in
[model resilience](model-resilience.md).
