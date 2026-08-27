# Web Operator

[English](web-operator.md)

Web Operator 是带不可变所有权声明与精确 Hostname 边界的 Agent Run。它使用正常 Browser
Activity、Approval、Sandbox 与 Evidence 协议，不存在单独浏览器工作流。

## Admission

Session 创建前，用户声明：

- `operator_scope`：`owned` 或 `third_party_saas`；
- `operator_domains`：一个或多个精确 Hostname。

Domain 规范化为小写 IDNA Hostname。URL、Path、Credential、Query、Fragment 与 Wildcard
都会被拒绝。取值写入 Session，并冻结到 Run Input。已有 Operator 声明的 Session 不能把
Domain List 编辑为空。

## Navigation 与 Action

每次绝对 HTTP(S) Navigation 与 Redirect 都在 Browser Adapter 内检查精确 Allowlist。
DNS/Private-Network Outbound Rule 仍然生效。Page Text 返回模型前会包裹成不可信外部内容。

Browser Read 是 Read-only。Navigation、Click、Input 等 Interactive Operation 具有
Non-read-only Policy，因此要求持久、逐 Invocation Approval。Approval 展示冻结 Tool Name/Risk；
聊天文本不能批准。用户可通过 VNC 检查或接管隔离 Chromium Desktop，但 VNC 操作不会伪造
Activity Result。

## Evidence

Operator Scope/Domain、Run Timeline、Approval Actor/Decision、Browser Activity Status、
Audit Chain 与授权 Screenshot/Artifact 会进入 Governance Profile 与签名 Evidence Package。
Secret 与 Browser Credential 原文会脱敏。

选择 Third-party SaaS 只记录用户声明，不授予额外能力，也不免除外部条款与法律义务。
