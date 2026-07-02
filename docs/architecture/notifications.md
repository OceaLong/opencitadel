# Notifications

- Persisted in `notifications` table
- Published to Redis channel `notify:{user_id}`
- UI subscribes via `GET /notifications/stream` (SSE)
- IM fallback via owner MCP `notify_channels` on scheduled jobs (fail-silent)
