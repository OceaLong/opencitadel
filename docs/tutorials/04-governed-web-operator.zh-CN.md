# 受治理 Web Operator

[English](04-governed-web-operator.md)

本教程使用内置 OpsConsole 作为企业自有浏览器目标。

## 启动

```bash
cp .env.example .env
# 设置必填 Secret，并配置支持工具调用的模型。
docker compose --profile local --profile demo up -d --build
```

OpenCitadel 位于 `http://localhost:8088`；OpsConsole 位于
`http://localhost:9099`（`agent` / `agent123`）。

## 运行

1. 选择 **Web Operator** Skill。
2. 要求它打开 OpsConsole、登录、检查一条工单并执行指定更新。
3. 在归属对话框选择**企业自有/自建**，保留精确允许 Host `ops-console, localhost`。
4. 启动 Session。

每次 Browser Navigation 都会检查冻结 Host List。Read-only 页面检查按只读 Policy 运行；
Navigation、Click、Input 等 Interactive Call 会生成持久 Approval Card。检查冻结 Tool/Risk
细节后，通过 Card Approve/Reject。Approval 是专用 Command，不是聊天短语。

需要检查或直接操作隔离浏览器时使用 VNC。VNC 交互不会把 Agent Pending Activity 标记完成；
Run 仍只通过正式 Result/Decision 协议推进。

## 验证

Run 终止后：

- 在 `/admin/audit` 验证 Audit Chain；
- 在 `/admin/compliance/sessions/{sessionId}` 查看 Run、Approval 与 Activity Timeline；
- 从 `/admin/compliance` 下载 Evidence Package，并检查 `manifest.json` 与
  `chain-signature.txt`。

若需定时执行，创建绑定该 Skill、精确 Operator Domain、Model 与可选 Resource Binding 的
Automation Job。每次 Firing 创建正式 Automation Run，并关联 Agent Run。

参见 [Web Operator 架构](../architecture/web-operator.zh-CN.md)。
