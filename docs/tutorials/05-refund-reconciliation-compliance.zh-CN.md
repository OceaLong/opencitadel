# 退款对账与合规稽核

[English](05-refund-reconciliation-compliance.md)

运行 OpsConsole Demo，在正式 Web Operator Approval/Evidence 协议下对账退款工单。

```bash
docker compose --profile local --profile demo up -d --build
```

1. 选择 Skill `refund-reconciliation`。
2. 声明企业自有 Host `ops-console, localhost`。
3. 要求 Agent 比对退款工单与结算账本、分类差异、纠正一条 `ORPHAN_SETTLEMENT`，并产出
   Artifact。
4. Read Operation 读取工单/账本；Browser Form Mutation 等待持久、逐 Invocation Approval。
   核对精确 Action 后 Approve/Reject。
5. 报告作为 Artifact Activity Result 写入并 Finalize。

预期差异类为 `MISSING_SETTLEMENT`、`AMOUNT_MISMATCH`、`DUPLICATE_REFUND` 与
`ORPHAN_SETTLEMENT`。

完成后在 Admin → Evidence Center 验证 Chain 并下载签名 ZIP。Session Governance Profile
显示权威 Run、Approval 与 Activity Timeline。Admin → Compliance Report 导出 Control Mapping；
Auditor 可读取这些 View，但不能执行或批准 Mutation。

常用 Endpoint：

- `GET /api/admin/audit/verify-chain`
- `GET /api/admin/evidence/sessions`
- `GET /api/admin/evidence/sessions/{id}/package`
- `GET /api/admin/compliance/report`

通过 `cd demo/ops-console && python -m pytest tests/` 验证 Demo Fixture。
