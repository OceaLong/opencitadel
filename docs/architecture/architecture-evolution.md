# Architecture Evolution and Scaling

[简体中文](architecture-evolution.zh-CN.md)

OpenCitadel starts as a modular deployment with PostgreSQL, disposable Redis
wake-ups, stateless API replicas, and horizontally scalable execution-kernel
replicas. Scale boundaries follow durable queues rather than product-domain
processes.

## Current scaling model

- API replicas scale on request latency/CPU and share PostgreSQL/object storage.
- Execution-kernel replicas claim commands, Activities, timers, outbox rows,
  and projector work with database fencing. Scale on pending age, throughput,
  provider latency, and sandbox capacity rather than CPU alone.
- Sandboxes scale independently with per-node/resource admission.
- PostgreSQL requires monitored connections, storage, locks, WAL, backups, and
  tested restore.
- Redis may be clustered for availability, but its loss never requires data
  restoration for execution correctness.
- Ops Collector and Actuator scale only within their narrow security roles.

## Evolution invariants

Any future queue, workflow engine, or service extraction must preserve:

1. one authoritative Run event stream and typed command idempotency;
2. invocation intent/call-start before every external call;
3. exact OwnerScope and least-privilege database/service roles;
4. approval as a durable command/event, not a transport message;
5. live/replay from the same sanitized public projection;
6. immutable resource versions and frozen session bindings;
7. deterministic recovery under duplicate delivery and process death.

## Extraction criteria

Extract a component only when load or trust boundaries require independent
scaling. The natural candidates are provider-specific Activity workers,
projector fleets, object processing, and sandbox brokers. Extraction uses
registered Activity contracts and object references; it does not create a
second lifecycle table.

Introducing Kafka or a managed workflow service is optional. It may improve
transport/operations but cannot replace domain idempotency, approval,
unknown-outcome handling, event integrity, or tenant isolation.

## Capacity signals

Track oldest pending command/Activity/timer/outbox, claim expiry, projector lag,
database saturation, object-store latency, provider quotas, and sandbox
admission. A growing durable pending age is the primary backpressure signal.
Autoscaling must have hard concurrency and provider-rate limits to avoid
amplifying an outage.
