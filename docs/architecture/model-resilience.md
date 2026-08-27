# Model Resilience

[简体中文](model-resilience.zh-CN.md)

Model calls have two explicit reliability layers with different scopes.

## Provider-call layer

`ResilientLLMClient` owns bounded attempts for one Activity execution. It uses
`model_resilience.max_attempts_per_call` and
`max_call_budget_seconds`, classifies transient transport/provider errors,
records circuit-breaker state, and may select an eligible configured fallback
model. Quota failures can move directly to another candidate. Cross-provider
fallback is disabled unless explicitly configured.

Streaming is retried or rerouted only before the first output chunk. Once
streaming begins, changing provider or repeating the request could duplicate
visible output, so the current call fails and the Activity protocol handles
the result.

The circuit breaker is operational protection, not workflow state. Opening a
circuit prevents a provider call but does not complete or cancel a Run.

## Activity layer

The execution kernel persists model-call input references, invocation identity,
claim generation, timeout, call-start, heartbeat, and result. An Activity
retry is a workflow decision and remains visible in the event stream. It never
depends on a process-local counter. A stale worker cannot submit a completion
for an older claim generation.

The two layers must not share a mutable retry counter: provider attempts are
bounded inside one invocation; Activity retry creates or advances durable
execution state.

## Failure codes

Public model failures use stable codes such as `MODEL_NOT_CONFIGURED`,
`MODEL_RATE_LIMITED`, `MODEL_TIMEOUT`, `MODEL_QUOTA_EXCEEDED`,
`MODEL_INVALID_REQUEST`, `MODEL_UNAVAILABLE`, and `INFRASTRUCTURE_FAILED`.
Provider error bodies are not exposed directly to public events. Operational
logs and metrics retain the diagnostic category and model id without logging
credentials.

## Configuration

The active Execution Policy `model_resilience` section controls:

- bounded attempts and wall-clock call budget;
- breaker window, threshold, open TTL, and half-open probe timeout;
- fallback enablement, quota behavior, and cross-provider permission;
- fast failure when a circuit is open.

Fallback candidates still pass OwnerScope, enabled-state, capability, and
provider-policy checks. A vision request cannot silently fall back to a model
without the required capability.

## Verification

Tests cover retry bounds, wall-clock exhaustion, breaker open/half-open paths,
quota fallback, capability filtering, streaming-after-first-chunk behavior,
multimodal text downgrade, and Activity duplicate/stale completion fencing.
