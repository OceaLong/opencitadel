# 退款对账与合规稽核

受治理的 Web Operator 退款对账场景，含不可篡改证据链与等保/ISO 合规导出。

## 前置条件

```bash
docker compose --profile local --profile demo up --build
```

- OpenCitadel: http://localhost:8088
- OpsConsole: http://localhost:9099（账号 `agent` / `agent123`）

## 操作流程

1. 选择 Skill **退款对账稽核**（`refund-reconciliation`）
2. 操作范围对话框：域名 `ops-console,localhost`，门控 **standard**
3. 示例指令：*对账 ops-console 退款工单与结算账本，找出差异并处理 ORPHAN_SETTLEMENT，产出对账报告*
4. Agent 流程：
   - 浏览器登录 ops-console 读取退款工单
   - 只读 API `GET /api/settlements` 或结算账本页
   - 按 `order_no` 对账并分类差异
   - **ORPHAN_SETTLEMENT** 仅通过网页表单纠正（退款确认 → 逐工具门控）
   - 用 `artifact_write` / `artifact_finalize` 交付对账报告
5. 会话完成后：**管理后台 → 证据中心** 校验链并下载 ZIP 证据包
6. **管理后台 → 合规报表** 生成等保2.0 + ISO27001 映射（JSON / MD / PDF）

## 对账规则

| 类型 | 条件 |
|------|------|
| MISSING_SETTLEMENT | 工单已退款，无结算行 |
| AMOUNT_MISMATCH | 退款金额与结算合计不符 |
| DUPLICATE_REFUND | 同一订单多条结算 |
| ORPHAN_SETTLEMENT | 有结算但工单未标退款（可通过 Web 纠正） |

## 审计员角色

管理员将用户 `global_role` 设为 `auditor`。审计员只读访问概览、审计日志、证据中心、合规报表。

## API

| 端点 | 用途 |
|------|------|
| `GET /api/admin/audit/verify-chain` | 全局链完整性 |
| `GET /api/admin/evidence/sessions` | 可出证会话列表 |
| `GET /api/admin/evidence/sessions/{id}/package` | 下载证据 ZIP |
| `GET /api/admin/compliance/report?format=json\|md\|pdf` | 合规报告 |

## 种子数据校验

```bash
cd demo/ops-console && python -m pytest tests/
```

`/api/reconciliation/expected` 应包含 4 类差异。
