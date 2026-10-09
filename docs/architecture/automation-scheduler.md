# Automation and Scheduler

[简体中文](automation-scheduler.zh-CN.md)

Scheduled definitions are product records; each firing is a formal Automation
Run that admits a linked Agent or Patrol Run.

![automation scheduler](../assets/diagrams/automation-scheduler.png)

The scheduler loop runs inside execution-kernel replicas. A short Redis leader
lease reduces duplicate polling, but it is not correctness state. Database row
locking, deterministic firing ids, command idempotency, and the active-Run
projection prevent duplicate admission. If Redis is unavailable, lease
acquisition/renewal fails and scheduler polling/maintenance waits; after lease
expiry, a healthy replica can take over when Redis is reachable.

## Triggers

- Cron and interval ticks use the scheduled `next_run_at` in the firing id.
- Manual triggers use a new explicit firing id.
- Webhooks verify `HMAC-SHA256(raw_body, secret)` and derive a body/time-window
  firing id. The secret is stored in a versioned encrypted envelope and shown
  only at creation/rotation.
- Every firing admits an Automation Run: Patrol-bound jobs link a Patrol child,
  while generic jobs link an Agent child. Both create a session.

Resource access and concrete active versions are validated/bound before the
command transaction commits. A job that already has an active formal Run is
not admitted again; the service uses the locked `last_run_status=running`
summary as its fast admission guard and reconciles it from the formal Run.

## Status and recovery

`last_run_*` fields are query summaries. `last_execution_run_id` links the job
to the authoritative Run projection. Reconciliation copies terminal Run state
to the summary and sends durable inbox notifications plus optional MCP IM.
Process death cannot manufacture a terminal state.

`GET /api/scheduled-jobs/{job_id}/runs` returns the paginated firing history for
a job (Run ID, family, status, creation/terminal times, and failure code) so an
operator can audit every past trigger, not only the latest summary. The leader
lease is continuously renewed for as long as a replica holds it, so a healthy
leader keeps polling without repeatedly re-acquiring; losing the lease simply
lets another replica take over.

The same leased loop runs bounded knowledge-base version GC and patrol
retention. These operations use independently renewed Redis leases plus transactional
database checks and never delete active/bound versions or audit rows. The
leader tick also runs recycle-bin and execution-queue retention; those maintenance
limits are deployment Settings. An admission failure before any Run exists may
write `last_run_status=failed` and emit a trigger-failure notification.

Live scheduler admission, polling, lease, concurrency, and webhook idempotency
are under `scheduler` in the Operations Policy. Job definitions live at `/automation`.
