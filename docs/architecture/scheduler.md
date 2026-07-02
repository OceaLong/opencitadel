# Scheduler architecture

Worker runs `run_scheduler_loop` with a Redis leader lease on key `scheduler:leader`.

## Leader election

- Each worker uses a unique ID (`hostname-uuid`).
- `SET scheduler:leader <worker_id> NX EX <lease>` acquires leadership.
- Non-leaders **must not** extend the lease.
- The current leader renews with `EXPIRE` only when `GET scheduler:leader == worker_id`.

## Due job polling

- Polls `scheduled_jobs` where `enabled`, `next_run_at <= now()`, and `last_run_status != running`.
- Skips webhook-triggered jobs in the poll loop (webhooks use HTTP endpoint).
- On trigger failure, sets `last_run_status=failed` and backs off `next_run_at`.

## Run lifecycle

| Phase | `last_run_status` | Notes |
|-------|-------------------|-------|
| Trigger | `running` | Creates session, dispatches Redis Stream task |
| Session completed | `completed` | Updated via `ScheduledJobService.on_session_terminal` |
| Session failed/cancelled | `failed` / `cancelled` | Same hook |

## Webhook security

- `POST /api/webhooks/{token}` requires header `X-Webhook-Signature`.
- Signature = `HMAC-SHA256(raw_body, webhook_secret)` hex digest.
- Secret stored encrypted (Fernet via `API_KEY_SECRET`); shown once on create/rotate.
- Idempotency Redis key: `webhook:idem:{token}:{sha256(body)}` — scoped per job token.
- Duplicate payload returns existing `session_id` with `{ duplicate: true }`.
- Missing/invalid signature → HTTP 401.

## Notifications

- Job start → inbox `job_started` (+ optional IM via configured MCP channels).
- Job completion → inbox `job_complete` (+ IM when channels configured).

See `SchedulerConfig` in `AppConfig`.
