# 管理员、审计员与合规

[English](admin-auditor-compliance.md)

平台管理与审计是不同权限。Admin 管理平台资源；Auditor 读取治理与证据，但不能修改产品或
执行状态。

## Read Model

合规 UI 只消费正式、按 Owner Scope 的投影：

- Session Metadata 与冻结的 Operator Scope/Domain；
- Run Family、State、创建与终止时间；
- Approval Request、Decision、Actor、Subject 与 Feedback；
- Activity Type、State、Attempt 与脱敏 Failure Code；
- Execution Event 与 Audit Chain 校验状态；
- Patrol Finding 与 Remediation Outcome。

这些 View 不从 UI Event 或 Audit 文本重建工作流状态。Run、Approval 与 Activity 行来自正式
执行投影；Audit Chain 提供独立 Action Evidence。

## 主要 Endpoint

- `GET /api/admin/governance/overview`：审批积压/结果、每日 Approval Request 与 Activity
  Failure、Patrol Trend、Remediation Status、Audit Chain Status。
- `GET /api/admin/governance/sessions/{id}/profile`：单 Session 的 Run、Approval、Activity
  与已验证 Chain Timeline。
- `GET /api/admin/evidence/sessions`：可出证 Session 与 Event 数量。
- `GET /api/admin/evidence/sessions/{id}/package`：签名、脱敏 Evidence Archive。
- `GET /api/admin/audit/verify-chain`：平台 Chain Verification。
- `GET /api/admin/audit/verify-chain/sessions/{session_id}`：Session Chain Verification。
- `GET /api/admin/compliance/report`：聚合合规报告，支持 `framework`、`start`、`end`
  与 `format=json|md|pdf`。PDF Renderer 不可用时返回 HTTP 501，错误键为
  `apiErrors.compliance.pdfUnavailable`。

跨 Owner Session 访问由服务端在 Auditor Authority 下解析；普通用户不能用这些 Endpoint 枚举
外部资源。

## Evidence Package

证据包由服务端代码生成，不调用 LLM；生成时间与 ZIP Metadata 使重复导出不保证字节一致。
它包含 Manifest、JSON/Markdown Governance Profile、Audit Material、授权范围内的 Artifact
Metadata/Content，以及 Renderer 可用时的 PDF Summary。Audit 与 Governance Export Data
做脱敏；授权 Artifact 原始字节不经过 Profile Redactor。Manifest 包含 Session Metadata、
Chain Verification Result 与 File Digest，其 HMAC Signature 支持离线完整性验证。

可选 PDF 能力缺失不会改变 Source Evidence，Package 会记录缺项。Chain Check 失败会
记录在 Profile/Manifest，仍可生成并签名导出。有效 Package Signature 证明导出字节未被
修改，不代表 Event/Audit Chain 已通过校验。

## UI

- `/admin/governance`：平台治理趋势。
- `/admin/compliance`：Evidence Session 与导出。
- `/admin/compliance/sessions/[sessionId]`：正式 Governance Profile。
- `/admin/audit`：Audit 搜索与 Chain Verification。

Auditor View 隐藏所有 Mutation Control。Admin Mutation Route 仍需 CSRF、明确 Role Check、
Scope Validation 与追加式 Audit Recording。
