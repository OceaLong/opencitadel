# Refund Reconciliation and Compliance

[简体中文](05-refund-reconciliation-compliance.zh-CN.md)

Run the OpsConsole demo and reconcile refund tickets under the formal Web
Operator approval/evidence protocol.

```bash
docker compose --profile local --profile demo up -d --build
```

1. Select Skill `refund-reconciliation`.
2. Declare enterprise-owned hosts `ops-console, localhost`.
3. Ask the Agent to compare refund tickets with the settlement ledger, classify
   discrepancies, correct one `ORPHAN_SETTLEMENT`, and produce an artifact.
4. Read operations gather ticket/ledger data. The browser form mutation waits
   for a persisted per-invocation approval. Verify the exact action and approve
   or reject it.
5. The report is written/finalized as an Artifact Activity result.

Expected discrepancy classes are `MISSING_SETTLEMENT`, `AMOUNT_MISMATCH`,
`DUPLICATE_REFUND`, and `ORPHAN_SETTLEMENT`.

After completion, use Admin → Evidence Center to verify the chain and download
the signed ZIP. The session governance profile shows the authoritative Run,
approval, and Activity timelines. Admin → Compliance Report exports the
control mapping; auditors can read these views but cannot execute or approve
mutations.

Useful endpoints:

- `GET /api/admin/audit/verify-chain`
- `GET /api/admin/evidence/sessions`
- `GET /api/admin/evidence/sessions/{id}/package`
- `GET /api/admin/compliance/report`

Verify demo fixtures with `cd demo/ops-console && python -m pytest tests/`.
