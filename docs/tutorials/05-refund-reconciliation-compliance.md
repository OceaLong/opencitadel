[简体中文](05-refund-reconciliation-compliance.zh-CN.md)

# Refund reconciliation & compliance audit

Governed refund reconciliation using Web Operator against the ops-console demo, with immutable audit evidence and compliance reporting.

## Prerequisites

```bash
docker compose --profile local --profile demo up --build
```

- OpenCitadel: http://localhost:8088
- OpsConsole: http://localhost:9099 (credentials: `agent` / `agent123`)

## Workflow

1. Create a session with skill **退款对账稽核** (`refund-reconciliation`).
2. In the operator scope dialog, set domains to `ops-console,localhost` and gate profile **standard**.
3. Prompt example: *对账 ops-console 退款工单与结算账本，找出差异并处理 ORPHAN_SETTLEMENT，产出对账报告*
4. Agent flow:
   - Browser: login ops-console, read refund tickets
   - Read-only API: `GET /api/settlements` (or settlement ledger page)
   - Match on `order_no`; classify discrepancies
   - Correct **ORPHAN_SETTLEMENT** via web form only (refund confirmation → HITL gate)
   - Deliver reconciliation report via `artifact_write` / `artifact_finalize`
5. After session completes, open **Admin → Evidence center** to verify chain and download ZIP evidence package.
6. **Admin → Compliance report** generates 等保2.0 + ISO27001 mapping (JSON / MD / PDF).

## Reconciliation rules

| Type | Condition |
|------|-----------|
| MISSING_SETTLEMENT | Ticket refunded, no settlement row |
| AMOUNT_MISMATCH | Refund amount ≠ settlement total |
| DUPLICATE_REFUND | Multiple settlements for same order |
| ORPHAN_SETTLEMENT | Settlement exists, ticket not marked refunded (correctable via web) |

## Auditor role

Set a user's `global_role` to `auditor` via admin user management. Auditors have read-only access to overview, audit log, evidence center, and compliance reports.

## API endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/admin/audit/verify-chain` | Global chain integrity |
| `GET /api/admin/evidence/sessions` | List evidence-eligible sessions |
| `GET /api/admin/evidence/sessions/{id}/package` | Download evidence ZIP |
| `GET /api/admin/compliance/report?format=json\|md\|pdf` | Compliance report |

## E2E seed verification

```bash
cd demo/ops-console && python -m pytest tests/
```

Expected: 4 discrepancy types in `/api/reconciliation/expected`.
