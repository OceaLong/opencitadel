# 10 分钟治理演示

[English](08-ten-minute-governance-demo.md)

这个 Compose Demo 覆盖只读 Patrol、Finding、持久 Approval、Chain Verification 与签名 Evidence。

## 1. 启动与 Seed

```bash
./scripts/quickstart.sh --demo
```

Demo Profile 启动 Ops Collector 与 OpsConsole，注册只读 Collector Policy，并创建
**Demo Governance Patrol**。如果未提供可选 `DEMO_INFERENCE_*` Seed 值，请在设置 → 推理中配置
一个支持 Tool Call 的 Chat Binding。

## 2. 运行 Patrol

打开 `/patrols`，选择 Demo Pack，点击**立即运行**。三条 Check 应通过。然后制造确定性
Dependency Finding：

```bash
docker compose stop ops-console
# 再运行 Pack，等待 Warning Finding。
docker compose start ops-console
```

Collector 返回注册 Evidence；由服务端 Assertion Engine 而非 LLM 决定 Check Failure。

## 3. 批准一次 Browser Action

1. 首页选择 **Web Operator**。
2. 要求它打开 `http://localhost:9099` 并登录。
3. 保留精确 Host `ops-console, localhost`，声明目标为企业自有。
4. Interactive Browser Activity 请求 Approval 时，检查冻结 Tool/Risk 细节并点击**批准**。

按钮提交专用 Approval Command，不会向 Chat 插入审批短语，而且只授权该持久 Invocation。

## 4. 验证 Evidence

- `/admin/governance` 展示 Approval/Activity 与 Patrol Trend。
- `/admin/audit` 验证追加式 Audit Hash Chain。
- `/admin/compliance/sessions/{sessionId}` 展示正式 Run、Approval、Activity Timeline 与
  Execution Chain Verification。
- `/admin/compliance` 下载带 Manifest File Digest 与 `chain-signature.txt` 的 ZIP。

Compose Demo 没有 Kubernetes Mutation Target。要运行真实
Proposal → Approval → Actuator → Verification 闭环，请在一次性集群使用
[审批制修复](07-approved-remediation.zh-CN.md)。
