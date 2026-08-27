# Admin, Auditor, and Compliance

[简体中文](admin-auditor-compliance.zh-CN.md)

Administration and audit are separate authorities. Administrators manage
platform resources; auditors read governance and evidence but cannot mutate
product or execution state.

## Read models

The compliance UI consumes formal, owner-scoped projections:

- session metadata and frozen Operator scope/domains;
- Run family, state, creation, and terminal time;
- approval request, decision, actor, subject, and feedback;
- Activity type, state, attempt, and sanitized failure code;
- execution-event and audit-chain verification status;
- patrol findings and remediation outcomes.

These views do not reconstruct workflow state from UI events or audit text.
Run, approval, and Activity rows come from the formal execution projections;
the audit chain supplies independent action evidence.

## Main endpoints

- `GET /api/admin/governance/overview`: approval backlog/outcomes, daily
  approval requests and Activity failures, patrol trend, remediation status,
  audit-chain status.
- `GET /api/admin/governance/sessions/{id}/profile`: one session's Run,
  approval, Activity, and verified-chain timeline.
- `GET /api/admin/evidence/sessions`: eligible sessions with event counts.
- `GET /api/admin/evidence/sessions/{id}/package`: signed, redacted evidence
  archive.
- `GET /api/admin/audit/verify-chain`: platform or session chain verification.
- `GET /api/admin/compliance/report`: aggregate compliance report.

Cross-owner session access is resolved server-side under auditor authority;
ordinary users cannot use these endpoints to enumerate foreign resources.

## Evidence package

The package is built deterministically without an LLM. It includes a manifest,
governance profile in JSON/Markdown, audit material, artifact metadata/content
when authorized, and a PDF summary when the renderer is available. Every
free-text field receives key-based redaction and secret-pattern scrubbing.
Manifest digests and an HMAC signature allow offline integrity checks.

Missing optional PDF support does not change the source evidence; the package
records the omission. Hash-chain or signature failure is surfaced as an error,
not replaced with a best-effort success.

## UI

- `/admin/governance` shows platform trends.
- `/admin/compliance` lists evidence sessions and exports.
- `/admin/compliance/sessions/[sessionId]` renders the formal governance
  profile.
- `/admin/audit` provides audit search and chain verification.

Auditor views hide all mutation controls. Admin mutation routes still require
CSRF protection, explicit role checks, scope validation, and append-only audit
recording.
