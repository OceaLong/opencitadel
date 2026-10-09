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
- `GET /api/admin/audit/verify-chain`: platform chain verification.
- `GET /api/admin/audit/verify-chain/sessions/{session_id}`: session chain verification.
- `GET /api/admin/compliance/report`: aggregate compliance report with `framework`,
  `start`, `end`, and `format=json|md|pdf` filters. Unavailable PDF rendering
  returns HTTP 501 with `apiErrors.compliance.pdfUnavailable`.

Cross-owner session access is resolved server-side under auditor authority;
ordinary users cannot use these endpoints to enumerate foreign resources.

## Evidence package

The package is built by server code without an LLM; generation time and ZIP
metadata mean repeated exports need not have identical bytes. It includes a manifest,
governance profile in JSON/Markdown, audit material, artifact metadata/content
when authorized, and a PDF summary when the renderer is available. Audit and
governance export data is redacted; authorized artifact bytes are included as
content and are not passed through that profile redactor. The manifest includes
session metadata, chain verification results, and file digests; its HMAC
signature supports offline integrity checks.

Missing optional PDF support does not change the source evidence; the package
records the omission. A failed chain check is recorded in the profile/manifest
and the export can still be generated and signed. A valid package signature
proves the exported bytes have not changed; it does not prove the event or
audit chain passed verification.

## UI

- `/admin/governance` shows platform trends.
- `/admin/compliance` lists evidence sessions and exports.
- `/admin/compliance/sessions/[sessionId]` renders the formal governance
  profile.
- `/admin/audit` provides audit search and chain verification.

Auditor views hide all mutation controls. Admin mutation routes still require
CSRF protection, explicit role checks, scope validation, and append-only audit
recording.
