[English](06-ops-patrol.md)

# 运行只读每日运维巡检

Ops Patrol 通过固定的只读 MCP Collector 检查自托管 Kubernetes 应用。阈值判定、Finding 创建和证据包签名均由服务端完成，不信任 Agent 自报结论。

## 开始前

需要准备：

- 正常运行的 API、Worker、PostgreSQL、Redis 与支持工具调用的模型；
- 使用专用只读 ServiceAccount 部署在目标集群的 Ops Collector；
- 已评审的 Namespace/Workload 白名单与注册探针；
- 已启用的 Streamable HTTP MCP Server，九个 Tool Policy 均固定为只读；
- 可启用全局功能开关的管理员；
- Operator 对目标工作区有权限。Auditor 可以复核，但不能创建、触发或决策 Finding。

仅验证本地传输时可以启动：

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
```

Compose Profile 不会挂载宿主机 kubeconfig。真实 Kubernetes 观察应使用 Helm/Kustomize ServiceAccount。绝不能将 Collector 暴露到公网。

## 准备 Collector

内置 `kubernetes-baseline-v1` 向导会创建十项已启用检查。验证前必须注册使用以下标识符的外部目标：

| 检查 | 必需注册 ID |
|------|-------------|
| PVC 使用率 | `pvc-utilization` Prometheus Query |
| HTTP 5xx 比例 | `app-5xx-ratio` Prometheus Query |
| Endpoint 健康 | `primary-endpoint` HTTP Probe |
| 证书过期 | `primary-tls` Certificate Probe |
| 备份新鲜度 | `primary-database` Backup Status |
| 依赖健康 | `primary-dependencies` Dependency Group |

四项 Kubernetes 检查使用 Pack Namespace 与 Collector Namespace/Workload 白名单。完整配置示例见 [Collector README](../../ops-collector/README.zh-CN.md#配置参考)。

在 **设置 → 集成** 中，把内部 URL（默认 Helm Release 为 `http://opencitadel-ops-collector:8090/mcp`）注册为 Streamable HTTP 并启用。然后使用 [Ops Patrol 运维手册](../operations/ops-patrol.zh-CN.md#注册-mcp-server) 中的已认证管理 API Payload 持久化九个 Tool Policy；当前 UI 表单不负责编辑这些 Policy。

管理员打开 **设置 → 运行时 → feature_flags**，启用 `enable_ops_patrol`，并保持 `enable_ops_patrol_fixture_replay` 关闭。启用 DB AppConfig 时，仅修改 `api/config.yaml` 不会覆盖已有全局配置行。

## 创建并验证 Pack

1. 打开 **Ops Patrol → 创建巡检**。
2. 选择 Collector。向导不接受任意 URL 或 PromQL。
3. 填写与 `OPS_COLLECTOR_TARGET_REF` 一致的稳定 Target Ref，以及 Cluster、Namespace、Environment。
4. 复核十项固定检查与阈值。Developer Preview 向导不编辑或禁用单项检查；若需要该控制，应通过 API 创建完整自定义 Pack Config。
5. 选择 IANA 时区和每日五段 Cron；第一次运行前先保持计划关闭。
6. 点击 **创建并 dry-run**。
7. 打开 Pack，检查 Validation Error、Enabled Tool、Capability Hash 与 Dry-run Summary。
8. 验证成功时向导会自动激活 Pack；失败时 Pack 保持非 Active。修复 Collector/配置后点击**重新验证**，检查新摘要，仅在当前 Version 通过后激活。

后续修改 Pack 会增加 Version、暂停计划，并要求重新验证/激活。

## 运行与复核

1. 点击 **立即运行**。UI 会发送唯一 `Idempotency-Key`，重复提交不会创建重复 Run。
2. 打开 Run，等待进入终态。
3. 逐项复核服务端计算的结果、观察字段、断言详情、证据引用与错误码。
4. 对每个可处理 Finding 执行**确认**、**已处理**或**误报**；误报原因必填。
5. 下载证据 ZIP，按运维手册验证 SHA-256 Manifest 与 HMAC。
6. 确认手动 Run 可信后再启用 Pack 计划，并核对 `next_run_at` 使用预期时区。

Developer Preview 有意不提供修复按钮，也不会授予 Collector 变更权限。

## 理解结果

| 状态 | 含义 |
|------|------|
| `pass` | 全部已配置服务端断言通过 |
| `warn` / `fail` | 阈值越界；可能创建或去重 Finding |
| `error` | Probe、Schema、Capability 或必需证据失败；不能视为健康 |
| `skipped` | 按 Pack 缺失数据契约显式跳过；不会静默计为通过 |

Pack 详情显示 30 天计划运行成功率、Finding/误报数与复核时间中位数。只有 Operator 打开 Run 并完成 Finding 决策后才计算复核时间。

## 安全回滚

在全局运行时设置中设 `feature_flags.enable_ops_patrol=false`。导航与新工作会隐藏，计划停止创建 Run，但已有且有权访问的 Run/证据仍可读取。重新启用不会丢失配置和历史。

部署、权限、证据验证、备份恢复与排障参见 [Ops Patrol 运维手册](../operations/ops-patrol.zh-CN.md)。
